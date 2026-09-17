from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import historical_context_formatter as formatter
import mrs_engagement_analytics as analytics
import mrs_log_digest as digest


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def snowflake(value: datetime, sequence: int = 0) -> str:
    milliseconds = int(value.timestamp() * 1000)
    return str(((milliseconds - analytics.X_SNOWFLAKE_EPOCH_MS) << 22) + sequence)


def paths(tmp_path: Path) -> analytics.AnalyticsPaths:
    project = tmp_path / "project"
    project.mkdir()
    return analytics.AnalyticsPaths.for_project(project)


def pair_record(*, age_days: int = 8, context: bool = True, suffix: int = 1) -> dict:
    main_time = NOW - timedelta(days=age_days, minutes=suffix)
    context_time = main_time + timedelta(seconds=2)
    quote = f"Canonical quote {suffix}"
    return {
        "quote_id": analytics.quote_text_hash(quote),
        "canonical_quote_hash": analytics.quote_text_hash(quote),
        "quote_text": quote,
        "main_post_id": snowflake(main_time, suffix),
        "main_posted_at": analytics.iso_utc(main_time),
        "context_post_id": snowflake(context_time, suffix) if context else None,
        "context_posted_at": analytics.iso_utc(context_time) if context else None,
        "context_missing_reason": None if context else "context_reply_not_recorded",
        "formatter_version": "historical_context_reply_schema_v1" if context else None,
        "verification_label": "Exact wording" if context else None,
        "source_class": "Hansard" if context else None,
        "historical_confidence": "high" if context else None,
        "context_weighted_character_count": 300 if context else None,
        "meaning_included": True if context else None,
        "shortening_applied": False if context else None,
        "image_source": "original",
        "image_filename": f"t{suffix:02d}.jpg",
        "image_score": 40.0 + suffix,
        "made_with_ai": False,
        "quotation_topic": "liberty",
        "discovery_sources": ["fixture"],
    }


def initialise_with_pair(tmp_path: Path, *, age_days: int = 8, context: bool = True):
    test_paths = paths(tmp_path)
    analytics.initialise_database(test_paths)
    connection = analytics.connect_database(test_paths)
    record = pair_record(age_days=age_days, context=context)
    analytics.apply_discovery(connection, [record], now=NOW)
    return test_paths, connection, record


def response_for(post_ids, *, status=200, metrics=True, errors=None):
    data = []
    if metrics:
        for index, post_id in enumerate(post_ids, start=1):
            data.append({
                "id": post_id,
                "public_metrics": {
                    "impression_count": 100 * index,
                    "like_count": 5,
                    "reply_count": 2,
                    "retweet_count": 1,
                    "quote_count": 1,
                    "bookmark_count": 3,
                },
                "non_public_metrics": {
                    "url_link_clicks": 4,
                    "user_profile_clicks": 2,
                    "engagement_count": 16,
                },
            })
    body = {"data": data}
    if errors is not None:
        body["errors"] = errors
    return analytics.ReadResult(
        status_code=status,
        body=body,
        headers={},
        endpoint="/2/tweets",
        request_id="request-1",
        elapsed_seconds=0.1,
    )


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def lookup_posts(self, post_ids):
        self.calls.append(list(post_ids))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result(post_ids)
        return result


def discovery_fixture(tmp_path: Path, monkeypatch):
    test_paths = paths(tmp_path)
    quote_one = "Freedom needs responsibility."
    quote_two = "Government should serve the people."
    quote_ids = [analytics.quote_text_hash(quote_one), analytics.quote_text_hash(quote_two)]
    packets = {
        quote_ids[0]: {"quote_id": quote_ids[0], "quote_text": quote_one, "research_confidence": "high"},
        quote_ids[1]: {"quote_id": quote_ids[1], "quote_text": quote_two, "research_confidence": "medium"},
    }
    monkeypatch.setattr(formatter, "load_and_validate_corpus", lambda _path: (packets, set()))
    monkeypatch.setattr(formatter, "format_context_reply", lambda packet: {
        "character_count": 250,
        "verification_label": "Exact wording",
        "source_class": "Hansard",
        "historical_confidence": packet["research_confidence"],
        "shortening_applied": False,
        "meaning_included": True,
    })
    test_paths.project_dir.joinpath("mrsMThatcher.txt").write_text(quote_one + "\n" + quote_two + "\n")
    test_paths.project_dir.joinpath("quote_analysis.json").write_text(json.dumps({"items": {
        quote_ids[0]: {"analysis": {"primary_topics": ["liberty"]}},
        quote_ids[1]: {"analysis": {"primary_topics": ["government"]}},
    }}))
    main_one_time = NOW - timedelta(hours=2)
    context_time = main_one_time + timedelta(seconds=2)
    main_two_time = NOW - timedelta(hours=1, minutes=30)
    main_one = snowflake(main_one_time, 1)
    context_one = snowflake(context_time, 2)
    main_two = snowflake(main_two_time, 3)
    test_paths.project_dir.joinpath("historical_context_reply_history.json").write_text(json.dumps({
        "schema_version": 1,
        "items": {main_one: {
            "status": "completed", "parent_post_id": main_one, "reply_post_id": context_one,
            "quote_id": quote_ids[0], "reply_epoch": int(context_time.timestamp()),
        }},
    }))
    state = {"tweet_cache": {
        main_one: {"post_type": "quote", "text": quote_one},
        main_two: {"post_type": "quote", "text": quote_two},
    }}
    test_paths.project_dir.joinpath("bot_state.json").write_text(json.dumps(state))
    london = analytics.ZoneInfo("Europe/London")

    def log_line(when, event):
        stamp = when.astimezone(london).strftime("%Y-%m-%d %H:%M:%S")
        return f"{stamp} INFO log_event:1 - EVENT {json.dumps(event, separators=(',', ':'))}\n"

    test_paths.project_dir.joinpath("mrsMThatcher.log").write_text(
        log_line(context_time, {
            "event": "historical_context_reply", "status": "completed", "parent_post_id": main_one,
            "quote_id": quote_ids[0], "character_count": 240, "verification_label": "Exact wording",
            "source_class": "Margaret Thatcher Foundation", "historical_confidence": "high",
            "shortening_applied": False,
        })
        + log_line(main_one_time, {
            "event": "main_post_posted", "lane": "quote_image", "post_id": main_one,
            "line_no": 0, "image_basename": "t01.jpg", "image_score": 50,
        })
        + log_line(main_two_time, {
            "event": "main_post_posted", "lane": "quote_image", "post_id": main_two,
            "line_no": 1, "image_basename": "tg_example.png", "image_score": 75,
        }),
        encoding="utf-8",
    )
    return test_paths, packets, (main_one, context_one, main_two)


def identity_correction_fixture(tmp_path: Path, monkeypatch):
    test_paths = paths(tmp_path)
    main_post_id = "2077121396186992800"
    context_post_id = "2077121398795907462"
    canonical_text = "Capitalism is the moral way of running an economy."
    derived_text = "It is free enterprise, which creates wealth, not meddling governments."
    canonical_id = "e28d24c49780a4d8a0c248097ee4962f941fdf1b2ec687a1bf52cddabc95995b"
    derived_id = "a8de2cdcaa20182b2129e0292e4c98956c770132336c12c7130963d3796876e6"
    assert analytics.quote_text_hash(canonical_text) == canonical_id
    assert analytics.quote_text_hash(derived_text) == derived_id
    packets = {
        canonical_id: {"quote_id": canonical_id, "quote_text": canonical_text, "research_confidence": "high"},
        derived_id: {"quote_id": derived_id, "quote_text": derived_text, "research_confidence": "high"},
    }
    monkeypatch.setattr(formatter, "load_and_validate_corpus", lambda _path: (packets, set()))
    monkeypatch.setattr(formatter, "format_context_reply", lambda packet: {
        "character_count": 420,
        "verification_label": "Exact wording",
        "source_class": "Margaret Thatcher Foundation",
        "historical_confidence": packet["research_confidence"],
        "shortening_applied": False,
        "meaning_included": True,
    })
    lines = [f"Unrelated retained quote {index}." for index in range(599)] + [derived_text]
    test_paths.project_dir.joinpath("mrsMThatcher.txt").write_text("\n".join(lines) + "\n")
    test_paths.project_dir.joinpath("quote_analysis.json").write_text(json.dumps({"items": {
        canonical_id: {"analysis": {"primary_topics": ["economy"]}},
        derived_id: {"analysis": {"primary_topics": ["enterprise"]}},
    }}))
    history = {
        "schema_version": 1,
        "items": {main_post_id: {
            "status": "completed",
            "parent_post_id": main_post_id,
            "reply_post_id": context_post_id,
            "quote_id": canonical_id,
        }},
    }
    test_paths.project_dir.joinpath("historical_context_reply_history.json").write_text(json.dumps(history))
    test_paths.project_dir.joinpath("bot_state.json").write_text(json.dumps({"marker": "must remain unchanged"}))
    event_time = analytics.snowflake_datetime(main_post_id)
    london = analytics.ZoneInfo("Europe/London")
    stamp = event_time.astimezone(london).strftime("%Y-%m-%d %H:%M:%S")
    events = [
        {"event": "historical_context_reply", "status": "completed", "parent_post_id": main_post_id,
         "quote_id": canonical_id, "character_count": 420},
        {"event": "main_post_posted", "lane": "quote_image", "post_id": main_post_id,
         "line_no": 599, "image_basename": "t14.jpg", "image_score": 25.4},
    ]
    test_paths.project_dir.joinpath("mrsMThatcher.log").write_text("".join(
        f"{stamp} INFO log_event:1 - EVENT {json.dumps(event, separators=(',', ':'))}\n" for event in events
    ))
    correction = {
        "schema_version": 1,
        "corrections": [{
            "correction_id": "stale-main-post-line-2077121396186992800-v1",
            "post_id": main_post_id,
            "source": "structured_log_main_post_event",
            "historical_line_no": 599,
            "observed_derived_quote_id": derived_id,
            "observed_derived_quote_text": derived_text,
            "canonical_quote_id": canonical_id,
            "canonical_quote_text": canonical_text,
            "classification": "incorrect_derived_analytics_record",
            "reason": "A historical line number shifted after an audited corpus cleanup.",
            "evidence": ["posting-time quote hash", "durable context history", "before/after source lines"],
            "created_at": "2026-07-18T00:00:00Z",
        }],
    }
    test_paths.identity_corrections.parent.mkdir(parents=True)
    test_paths.identity_corrections.write_text(json.dumps(correction))
    return test_paths, packets, (main_post_id, context_post_id, canonical_id, derived_id)


def legacy_whitespace_hash_fixture(
    tmp_path: Path,
    monkeypatch,
    *,
    age_days: int,
    include_history: bool = True,
    include_ledger: bool = True,
) -> tuple[analytics.AnalyticsPaths, dict, dict]:
    """Build the historical raw-hash/legacy-normalised-hash incident shape."""
    test_paths = paths(tmp_path)
    quote_text = (
        "A free society depends on people choosing for themselves.  "
        "That choice carries responsibility."
    )
    raw_quote_id = hashlib.sha256(quote_text.encode("utf-8")).hexdigest()
    legacy_quote_id = analytics.quote_text_hash(quote_text)
    assert raw_quote_id != legacy_quote_id

    packets = {
        raw_quote_id: {
            "quote_id": raw_quote_id,
            "quote_text": quote_text,
            "research_confidence": "high",
        },
    }
    monkeypatch.setattr(
        formatter,
        "load_and_validate_corpus",
        lambda _path: (packets, set()),
    )
    monkeypatch.setattr(
        formatter,
        "format_context_reply",
        lambda packet: {
            "character_count": 320,
            "verification_label": "Exact wording",
            "source_class": "Margaret Thatcher Foundation",
            "historical_confidence": packet["research_confidence"],
            "shortening_applied": False,
            "meaning_included": True,
        },
    )

    main_time = NOW - timedelta(days=age_days, hours=1)
    context_time = main_time + timedelta(seconds=2)
    main_post_id = snowflake(main_time, 201)
    context_post_id = snowflake(context_time, 202)
    test_paths.project_dir.joinpath("mrsMThatcher.txt").write_text(
        quote_text + "\n",
        encoding="utf-8",
    )
    test_paths.project_dir.joinpath("quote_analysis.json").write_text(
        json.dumps({"items": {raw_quote_id: {"analysis": {"primary_topics": ["freedom"]}}}}),
        encoding="utf-8",
    )
    test_paths.project_dir.joinpath("bot_state.json").write_text(
        json.dumps({"marker": "must remain unchanged"}),
        encoding="utf-8",
    )
    history_path = test_paths.project_dir / "historical_context_reply_history.json"
    history_path.write_text(
        json.dumps({
            "schema_version": 1,
            "items": {
                main_post_id: {
                    "status": "completed",
                    "parent_post_id": main_post_id,
                    "reply_post_id": context_post_id,
                    "quote_id": raw_quote_id,
                },
            } if include_history else {},
        }),
        encoding="utf-8",
    )

    event = {
        "event": "main_post_posted",
        "lane": "quote_image",
        "post_id": main_post_id,
        "quote_hash": legacy_quote_id,
        "line_no": 0,
        "image_basename": "t01.jpg",
        "image_score": 50,
    }
    london = analytics.ZoneInfo("Europe/London")
    stamp = main_time.astimezone(london).strftime("%Y-%m-%d %H:%M:%S")
    log_path = test_paths.project_dir / "mrsMThatcher.log"
    log_path.write_text(
        f"{stamp} INFO log_event:1 - EVENT {json.dumps(event, separators=(',', ':'))}\n",
        encoding="utf-8",
    )

    analytics.initialise_database(test_paths)
    ledger_record = {
        **pair_record(age_days=age_days, context=True, suffix=201),
        "quote_id": raw_quote_id,
        "canonical_quote_hash": raw_quote_id,
        "quote_text": quote_text,
        "main_post_id": main_post_id,
        "main_posted_at": analytics.iso_utc(main_time),
        "context_post_id": context_post_id,
        "context_posted_at": analytics.iso_utc(context_time),
        "quotation_topic": "freedom",
    }
    connection = analytics.connect_database(test_paths)
    try:
        if include_ledger:
            analytics.apply_discovery(connection, [ledger_record], now=main_time)
    finally:
        connection.close()

    evidence = {
        "quote_text": quote_text,
        "raw_quote_id": raw_quote_id,
        "legacy_quote_id": legacy_quote_id,
        "main_post_id": main_post_id,
        "context_post_id": context_post_id,
        "event": event,
        "packets": packets,
        "history_path": history_path,
        "log_path": log_path,
    }
    return test_paths, packets, evidence


def project_durable_state(test_paths: analytics.AnalyticsPaths) -> dict:
    """Snapshot synthetic source files and the logical analytics database."""
    connection = sqlite3.connect(
        f"file:{test_paths.database}?mode=ro",
        uri=True,
    )
    try:
        database_dump = tuple(connection.iterdump())
    finally:
        connection.close()
    files = {
        str(path.relative_to(test_paths.project_dir)): path.read_bytes()
        for path in sorted(test_paths.project_dir.rglob("*"))
        if path.is_file()
        and not path.name.endswith(("-shm", "-wal"))
    }
    return {"database_dump": database_dump, "files": files}


def replace_fixture_log_event(evidence: dict, event: dict) -> None:
    """Replace the fixture's one structured event without changing its time."""
    prefix = evidence["log_path"].read_text(encoding="utf-8").split("EVENT ", 1)[0]
    evidence["log_path"].write_text(
        f"{prefix}EVENT {json.dumps(event, separators=(',', ':'))}\n",
        encoding="utf-8",
    )


def test_deterministic_structured_discovery_precedence_and_missing_context(tmp_path, monkeypatch):
    test_paths, _packets, ids = discovery_fixture(tmp_path, monkeypatch)
    first = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    second = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    assert first == second
    assert [row["main_post_id"] for row in first] == [ids[0], ids[2]]
    assert first[0]["context_post_id"] == ids[1]
    assert first[0]["source_class"] == "Margaret Thatcher Foundation"
    assert first[0]["context_weighted_character_count"] == 240
    assert first[1]["context_post_id"] is None
    assert first[1]["context_missing_reason"] == "context_reply_not_recorded"
    assert first[1]["image_source"] == "generated" and first[1]["made_with_ai"] is True


def test_discovery_preserves_v2_formatter_version_from_durable_history(tmp_path, monkeypatch):
    test_paths, _packets, ids = discovery_fixture(tmp_path, monkeypatch)
    history_path = test_paths.project_dir / "historical_context_reply_history.json"
    history = json.loads(history_path.read_text())
    history["items"][ids[0]]["formatter_metadata"] = {
        "formatter_version": formatter.HISTORICAL_CONTEXT_FORMATTER_V2,
        "template_variant": "compact_with_meaning",
        "meaning_included": True,
        "meaning_decision_reason": "Meaning adds a distinct mechanism.",
        "raw_character_count": 220,
        "weighted_character_count": 205,
        "verification_label": "Exact wording",
        "source_class": "Hansard",
        "historical_confidence": "high",
        "shortening_applied": False,
    }
    history_path.write_text(json.dumps(history))
    monkeypatch.setattr(formatter, "format_context_reply_v2", lambda packet: {
        "character_count": 205,
        "verification_label": "Exact wording",
        "source_class": "Hansard",
        "historical_confidence": packet["research_confidence"],
        "shortening_applied": False,
        "meaning_included": True,
    })

    pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    pair = next(row for row in pairs if row["main_post_id"] == ids[0])
    assert pair["formatter_version"] == formatter.HISTORICAL_CONTEXT_FORMATTER_V2
    assert pair["context_weighted_character_count"] == 205
    assert pair["source_class"] == "Hansard"


def test_discovery_rejects_conflicting_quote_identity(tmp_path, monkeypatch):
    test_paths, packets, ids = discovery_fixture(tmp_path, monkeypatch)
    history = json.loads((test_paths.project_dir / "historical_context_reply_history.json").read_text())
    history["items"][ids[0]]["quote_id"] = next(key for key in packets if key != history["items"][ids[0]]["quote_id"])
    (test_paths.project_dir / "historical_context_reply_history.json").write_text(json.dumps(history))
    with pytest.raises(analytics.IdentityConflict, match="quote_id conflict"):
        analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)


def test_discovery_accepts_exact_corpus_identity_with_repeated_whitespace(
    tmp_path,
    monkeypatch,
):
    test_paths = paths(tmp_path)
    quote_text = (
        "In a free society, people can give away as much as they want to and "
        "whenever they want to. They don't have to say, 'I will only give mine "
        "away, if I can compel you to give yours away too.'  "
        "If they believe in pooling their possessions with others, they're "
        "welcome to do so."
    )
    quote_id = hashlib.sha256(quote_text.encode("utf-8")).hexdigest()

    assert quote_id == (
        "b301858e2ba14514c52ef64b217348cfceabe769c1530033761a2fef8c4304e8"
    )
    assert analytics.quote_text_hash(quote_text) != quote_id

    packets = {
        quote_id: {
            "quote_id": quote_id,
            "quote_text": quote_text,
            "research_confidence": "high",
        },
    }
    monkeypatch.setattr(
        formatter,
        "load_and_validate_corpus",
        lambda _path: (packets, set()),
    )
    monkeypatch.setattr(
        formatter,
        "format_context_reply",
        lambda _packet: {
            "character_count": 487,
            "verification_label": "Historically verified variant",
            "source_class": "Margaret Thatcher Foundation",
            "historical_confidence": "high",
            "shortening_applied": False,
            "meaning_included": True,
        },
    )

    main_time = NOW - timedelta(hours=1)
    context_time = main_time + timedelta(seconds=1)
    main_post_id = snowflake(main_time, 101)
    context_post_id = snowflake(context_time, 102)

    test_paths.project_dir.joinpath("mrsMThatcher.txt").write_text(
        quote_text + "\n",
        encoding="utf-8",
    )
    test_paths.project_dir.joinpath("quote_analysis.json").write_text(
        json.dumps({"items": {}}),
        encoding="utf-8",
    )
    test_paths.project_dir.joinpath("bot_state.json").write_text(
        json.dumps({}),
        encoding="utf-8",
    )
    test_paths.project_dir.joinpath(
        "historical_context_reply_history.json"
    ).write_text(
        json.dumps({
            "schema_version": 1,
            "items": {
                main_post_id: {
                    "status": "completed",
                    "parent_post_id": main_post_id,
                    "reply_post_id": context_post_id,
                    "quote_id": quote_id,
                },
            },
        }),
        encoding="utf-8",
    )

    pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)

    assert len(pairs) == 1
    assert pairs[0]["quote_id"] == quote_id
    assert pairs[0]["quote_text"] == quote_text

    # Persist the discovered pair, then prove that the next collector run can
    # replay the canonical raw-hash identity from the durable ledger.
    analytics.initialise_database(test_paths)
    connection = analytics.connect_database(test_paths)
    try:
        analytics.apply_discovery(connection, pairs, now=NOW)
    finally:
        connection.close()

    replayed = analytics.discover_post_pairs(
        test_paths,
        since_days=1,
        now=NOW,
    )

    assert len(replayed) == 1
    assert replayed[0]["quote_id"] == quote_id
    assert replayed[0]["quote_text"] == quote_text
    assert "engagement_post_pair_ledger" in replayed[0]["discovery_sources"]


def test_discovery_excludes_old_equivalent_legacy_hash_without_mutating_state(
    tmp_path,
    monkeypatch,
):
    test_paths, _packets, _evidence = legacy_whitespace_hash_fixture(
        tmp_path,
        monkeypatch,
        age_days=17,
    )
    before = project_durable_state(test_paths)

    # The live failure happened while merging an old history/event candidate,
    # before the normal 14-day output cutoff could exclude it.
    assert analytics.discover_post_pairs(
        test_paths,
        since_days=14,
        now=NOW,
    ) == []

    assert project_durable_state(test_paths) == before


def test_discovery_ignores_old_unrelated_event_hash_before_identity_merge(
    tmp_path,
    monkeypatch,
):
    test_paths, _packets, evidence = legacy_whitespace_hash_fixture(
        tmp_path,
        monkeypatch,
        age_days=17,
    )
    event = {
        **evidence["event"],
        "quote_hash": analytics.quote_text_hash("A genuinely unrelated quotation."),
    }
    replace_fixture_log_event(evidence, event)
    before = project_durable_state(test_paths)

    assert analytics.discover_post_pairs(
        test_paths,
        since_days=14,
        now=NOW,
    ) == []

    assert project_durable_state(test_paths) == before


def test_discovery_accepts_in_window_equivalent_legacy_hash_as_raw_identity(
    tmp_path,
    monkeypatch,
):
    test_paths, _packets, evidence = legacy_whitespace_hash_fixture(
        tmp_path,
        monkeypatch,
        age_days=8,
    )
    before = project_durable_state(test_paths)

    pairs = analytics.discover_post_pairs(test_paths, since_days=14, now=NOW)

    assert len(pairs) == 1
    assert pairs[0]["main_post_id"] == evidence["main_post_id"]
    assert pairs[0]["quote_id"] == evidence["raw_quote_id"]
    assert pairs[0]["canonical_quote_hash"] == evidence["raw_quote_id"]
    assert pairs[0]["quote_text"] == evidence["quote_text"]
    assert {
        "historical_context_reply_history",
        "engagement_post_pair_ledger",
        "structured_log_main_post_event",
    }.issubset(pairs[0]["discovery_sources"])
    assert "stale_main_post_line_number_ignored" not in pairs[0]["discovery_sources"]
    assert project_durable_state(test_paths) == before


@pytest.mark.parametrize("line_matches_event", [True, False])
def test_event_only_hash_precedes_mutable_current_line(
    tmp_path,
    monkeypatch,
    line_matches_event,
):
    test_paths, packets, evidence = legacy_whitespace_hash_fixture(
        tmp_path,
        monkeypatch,
        age_days=8,
        include_history=False,
        include_ledger=False,
    )
    expected_id = evidence["legacy_quote_id"]
    expected_text = evidence["quote_text"]
    if not line_matches_event:
        second_text = "A different eligible quotation."
        second_id = hashlib.sha256(second_text.encode("utf-8")).hexdigest()
        packets[second_id] = {
            "quote_id": second_id,
            "quote_text": second_text,
            "research_confidence": "high",
        }
        replace_fixture_log_event(
            evidence,
            {**evidence["event"], "quote_hash": second_id},
        )
        expected_id = second_id
        expected_text = second_text
    before = project_durable_state(test_paths)

    pairs = analytics.discover_post_pairs(test_paths, since_days=14, now=NOW)

    assert len(pairs) == 1
    assert pairs[0]["quote_id"] == expected_id
    assert pairs[0]["canonical_quote_hash"] == expected_id
    assert pairs[0]["quote_text"] == expected_text
    assert (
        "stale_main_post_line_number_ignored" in pairs[0]["discovery_sources"]
    ) is (not line_matches_event)

    assert project_durable_state(test_paths) == before


def test_event_only_conflicting_hashes_fail_without_a_current_line(
    tmp_path,
    monkeypatch,
):
    test_paths, _packets, evidence = legacy_whitespace_hash_fixture(
        tmp_path,
        monkeypatch,
        age_days=8,
        include_history=False,
        include_ledger=False,
    )
    first_event = {**evidence["event"], "line_no": 999}
    second_event = {
        **first_event,
        "quote_hash": analytics.quote_text_hash("A conflicting event identity."),
    }
    prefix = evidence["log_path"].read_text(encoding="utf-8").split("EVENT ", 1)[0]
    evidence["log_path"].write_text(
        "".join(
            f"{prefix}EVENT {json.dumps(event, separators=(',', ':'))}\n"
            for event in (first_event, second_event)
        ),
        encoding="utf-8",
    )
    before = project_durable_state(test_paths)

    with pytest.raises(analytics.IdentityConflict, match="quote_id conflict"):
        analytics.discover_post_pairs(test_paths, since_days=14, now=NOW)

    assert project_durable_state(test_paths) == before


def test_discovery_rejects_unrelated_structured_event_hash_despite_raw_evidence(
    tmp_path,
    monkeypatch,
):
    test_paths, _packets, evidence = legacy_whitespace_hash_fixture(
        tmp_path,
        monkeypatch,
        age_days=8,
    )
    event = {
        **evidence["event"],
        "quote_hash": analytics.quote_text_hash("A genuinely unrelated quotation."),
    }
    replace_fixture_log_event(evidence, event)
    before = project_durable_state(test_paths)

    with pytest.raises(
        analytics.IdentityConflict,
        match="quote_id conflict",
    ):
        analytics.discover_post_pairs(test_paths, since_days=14, now=NOW)

    assert project_durable_state(test_paths) == before




@pytest.mark.parametrize("authority_problem", ["missing_packet", "malformed_packet"])
def test_discovery_rejects_legacy_hash_without_exact_authoritative_text(
    tmp_path,
    monkeypatch,
    authority_problem,
):
    test_paths, packets, evidence = legacy_whitespace_hash_fixture(
        tmp_path,
        monkeypatch,
        age_days=8,
    )
    if authority_problem == "missing_packet":
        packets.clear()
    else:
        packets[evidence["raw_quote_id"]]["quote_text"] = (
            evidence["quote_text"] + " Materially changed."
        )
    before = project_durable_state(test_paths)

    with pytest.raises(analytics.IdentityConflict):
        analytics.discover_post_pairs(test_paths, since_days=14, now=NOW)

    assert project_durable_state(test_paths) == before


def test_exact_historical_line_shift_correction_preserves_one_canonical_pair_and_history(
    tmp_path, monkeypatch, caplog
):
    test_paths, _packets, ids = identity_correction_fixture(tmp_path, monkeypatch)
    main_post_id, context_post_id, canonical_id, derived_id = ids
    protected_paths = [
        test_paths.project_dir / "mrsMThatcher.txt",
        test_paths.project_dir / "historical_context_reply_history.json",
        test_paths.project_dir / "bot_state.json",
    ]
    before = {path: path.read_bytes() for path in protected_paths}

    caplog.set_level("WARNING", logger="mrs_engagement_analytics")
    pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    assert len(pairs) == 1
    assert pairs[0]["main_post_id"] == main_post_id
    assert pairs[0]["context_post_id"] == context_post_id
    assert pairs[0]["quote_id"] == canonical_id
    assert pairs[0]["canonical_quote_hash"] == canonical_id
    assert pairs[0]["quote_text"] == "Capitalism is the moral way of running an economy."
    assert derived_id not in {pair["quote_id"] for pair in pairs}
    assert pairs[0]["discovery_sources"].count(
        "quote_identity_correction:stale-main-post-line-2077121396186992800-v1"
    ) == 1
    assert sum("Applied audited quote identity correction" in record.message for record in caplog.records) == 1

    analytics.initialise_database(test_paths)
    with analytics.connect_database(test_paths) as connection:
        first = analytics.apply_discovery(connection, pairs, now=NOW)
        schedule_count = connection.execute("SELECT COUNT(*) FROM snapshot_schedule").fetchone()[0]
        client = FakeClient([lambda post_ids: response_for(post_ids)])
        collected = analytics.collect_due_snapshots(
            test_paths,
            connection,
            execute_read=True,
            max_api_requests=1,
            now=NOW,
            client_factory=lambda: client,
        )
        snapshot_count = connection.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0]
        assert collected["completed_snapshots"] == 4
        assert snapshot_count == 4
        second_pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
        second = analytics.apply_discovery(connection, second_pairs, now=NOW)
        third_pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
        third = analytics.apply_discovery(connection, third_pairs, now=NOW)
        assert first["inserted"] == 1
        assert second == {"inserted": 0, "updated": 1, "unchanged": 0, "total_input": 1}
        assert third == {"inserted": 0, "updated": 0, "unchanged": 1, "total_input": 1}
        assert connection.execute("SELECT COUNT(*) FROM post_pairs").fetchone()[0] == 1
        assert connection.execute("SELECT quote_id FROM post_pairs").fetchone()[0] == canonical_id
        assert connection.execute("SELECT COUNT(*) FROM snapshot_schedule").fetchone()[0] == schedule_count
        assert connection.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0] == snapshot_count
    assert {path: path.read_bytes() for path in protected_paths} == before


def test_identity_correction_cannot_cross_post_ids(tmp_path, monkeypatch, caplog):
    test_paths, _packets, ids = identity_correction_fixture(tmp_path, monkeypatch)
    canonical_id = ids[2]
    other_post_id = snowflake(NOW - timedelta(hours=1), 99)
    history_path = test_paths.project_dir / "historical_context_reply_history.json"
    history = json.loads(history_path.read_text())
    history["items"][other_post_id] = {"status": "failed", "parent_post_id": other_post_id, "quote_id": canonical_id}
    history_path.write_text(json.dumps(history))
    london = analytics.ZoneInfo("Europe/London")
    stamp = analytics.snowflake_datetime(other_post_id).astimezone(london).strftime("%Y-%m-%d %H:%M:%S")
    event = {"event": "main_post_posted", "lane": "quote_image", "post_id": other_post_id,
             "line_no": 599, "image_basename": "t14.jpg", "image_score": 25.4}
    with test_paths.project_dir.joinpath("mrsMThatcher.log").open("a") as handle:
        handle.write(f"{stamp} INFO log_event:1 - EVENT {json.dumps(event, separators=(',', ':'))}\n")
    caplog.set_level("WARNING", logger="mrs_engagement_analytics")
    pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    other = next(pair for pair in pairs if pair["main_post_id"] == other_post_id)
    assert other["quote_id"] == canonical_id
    assert not any(source.startswith("quote_identity_correction:") for source in other["discovery_sources"])
    assert "stale_main_post_line_number_ignored" in other["discovery_sources"]
    correction_messages = [record.message for record in caplog.records if "Applied audited quote identity correction" in record.message]
    assert len(correction_messages) == 1 and ids[0] in correction_messages[0]


@pytest.mark.parametrize(
    "replacement_text",
    [
        "A materially altered quotation now occupies the registered historical line.",
        "Capitalism is the moral way of running an economy.",
    ],
)
def test_registered_identity_correction_fails_closed_when_observed_text_changes(
    tmp_path,
    monkeypatch,
    replacement_text,
):
    test_paths, _packets, _ids = identity_correction_fixture(tmp_path, monkeypatch)
    lines = test_paths.project_dir.joinpath("mrsMThatcher.txt").read_text().splitlines()
    lines[599] = replacement_text
    test_paths.project_dir.joinpath("mrsMThatcher.txt").write_text("\n".join(lines) + "\n")

    with pytest.raises(analytics.IdentityConflict, match="correction evidence mismatch"):
        analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)


def test_identity_correction_cannot_reintroduce_noncanonical_quote(tmp_path, monkeypatch):
    test_paths, packets, _ids = identity_correction_fixture(tmp_path, monkeypatch)
    correction = json.loads(test_paths.identity_corrections.read_text())
    excluded_text = "Excluded attribution record."
    correction["corrections"][0]["canonical_quote_text"] = excluded_text
    correction["corrections"][0]["canonical_quote_id"] = analytics.quote_text_hash(excluded_text)
    test_paths.identity_corrections.write_text(json.dumps(correction))
    assert correction["corrections"][0]["canonical_quote_id"] not in packets
    with pytest.raises(analytics.AnalyticsError, match="eligible canonical packet"):
        analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)


def test_durable_pair_ledger_preserves_pre_cleanup_identity_and_snapshots(tmp_path, monkeypatch):
    test_paths, _packets, ids = discovery_fixture(tmp_path, monkeypatch)
    initial = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    analytics.initialise_database(test_paths)
    with analytics.connect_database(test_paths) as connection:
        analytics.apply_discovery(connection, initial, now=NOW)
        schedule_count = connection.execute("SELECT COUNT(*) FROM snapshot_schedule").fetchone()[0]

    # Simulate a retained historical line number after a source cleanup shifts
    # another valid quotation into that position, with the old tweet cache gone.
    quote_one = "Freedom needs responsibility."
    quote_two = "Government should serve the people."
    test_paths.project_dir.joinpath("mrsMThatcher.txt").write_text(
        "New retained prefix.\n" + quote_one + "\n" + quote_two + "\n"
    )
    test_paths.project_dir.joinpath("bot_state.json").write_text(json.dumps({"tweet_cache": {}}))

    rediscovered = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    second = next(pair for pair in rediscovered if pair["main_post_id"] == ids[2])
    assert second["quote_id"] == analytics.quote_text_hash(quote_two)
    assert "engagement_post_pair_ledger" in second["discovery_sources"]
    assert "stale_main_post_line_number_ignored" in second["discovery_sources"]
    with analytics.connect_database(test_paths) as connection:
        result = analytics.apply_discovery(connection, rediscovered, now=NOW)
        assert result["inserted"] == 0
        assert connection.execute("SELECT COUNT(*) FROM snapshot_schedule").fetchone()[0] == schedule_count
        assert connection.execute(
            "SELECT quote_id FROM post_pairs WHERE main_post_id = ?", (ids[2],)
        ).fetchone()[0] == analytics.quote_text_hash(quote_two)


def test_discovery_rejects_context_linked_to_two_mains(tmp_path, monkeypatch):
    test_paths, _packets, ids = discovery_fixture(tmp_path, monkeypatch)
    history = json.loads((test_paths.project_dir / "historical_context_reply_history.json").read_text())
    history["items"][ids[2]] = {
        "status": "completed", "parent_post_id": ids[2], "reply_post_id": ids[1],
        "quote_id": analytics.quote_text_hash("Government should serve the people."),
    }
    (test_paths.project_dir / "historical_context_reply_history.json").write_text(json.dumps(history))
    with pytest.raises(analytics.IdentityConflict, match="linked to both"):
        analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)


def test_context_receipt_alone_supplies_reply_timestamp(tmp_path, monkeypatch):
    test_paths, packets, ids = discovery_fixture(tmp_path, monkeypatch)
    history_path = test_paths.project_dir / "historical_context_reply_history.json"
    history_path.write_text(json.dumps({"schema_version": 1, "items": {}}))
    (test_paths.project_dir / "historical_context_reply_receipt.json").write_text(json.dumps({
        "schema_version": 1,
        "parent_post_id": ids[0],
        "reply_post_id": ids[1],
        "quote_id": next(iter(packets)),
        "status": "confirmed",
    }))

    pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    pair = next(row for row in pairs if row["main_post_id"] == ids[0])
    assert pair["context_post_id"] == ids[1]
    assert pair["context_posted_at"] == analytics.iso_utc(analytics.snowflake_datetime(ids[1]))

    analytics.initialise_database(test_paths)
    with analytics.connect_database(test_paths) as connection:
        analytics.apply_discovery(connection, [pair], now=NOW)
        stored = connection.execute("SELECT posted_at FROM posts WHERE role='historical_context'").fetchone()
        assert stored[0] == pair["context_posted_at"]


def test_idempotent_discovery_fixed_schedule_and_constraints(tmp_path):
    test_paths, connection, record = initialise_with_pair(tmp_path)
    assert connection.execute("SELECT COUNT(*) FROM snapshot_schedule").fetchone()[0] == 10
    result = analytics.apply_discovery(connection, [record], now=NOW)
    assert result == {"inserted": 0, "updated": 0, "unchanged": 1, "total_input": 1}
    assert connection.execute("SELECT COUNT(*) FROM post_pairs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM snapshot_schedule").fetchone()[0] == 10
    targets = [row[0] for row in connection.execute("SELECT DISTINCT target_age_seconds FROM snapshot_schedule ORDER BY 1")]
    assert targets == list(analytics.SNAPSHOT_TARGETS)
    connection.close()


def test_reinitialise_reports_preserved_collector_state(tmp_path):
    test_paths = paths(tmp_path)
    first = analytics.initialise_database(test_paths)
    with analytics.connect_database(test_paths) as connection:
        analytics.set_state_value(connection, "last_successful_collection", "2026-07-15T10:00:00Z", now=NOW)
        analytics.sync_state_file(test_paths, connection)
    preserved = json.loads(test_paths.state.read_text())

    second = analytics.initialise_database(test_paths)
    assert second == preserved
    assert second["initialised_at"] == first["initialised_at"]
    assert second["last_successful_collection"] == "2026-07-15T10:00:00Z"


def test_late_snapshots_are_planned_once_and_success_is_archived_once(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    client = FakeClient([lambda ids: response_for(ids)])
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: client,
    )
    assert result["requests_made"] == 1
    assert result["completed_snapshots"] == 10
    assert len(client.calls) == 1 and len(client.calls[0]) == 2
    assert connection.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0] == 10
    actual_ages = {row[0] for row in connection.execute("SELECT DISTINCT actual_age_seconds FROM metric_snapshots")}
    assert all(age >= 168 * 3600 for age in actual_ages)
    assert len(list(test_paths.raw_responses.glob("*.json"))) == 1
    second = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: pytest.fail("completed schedules must not call X"),
    )
    assert second["status"] == "nothing_due" and second["requests_made"] == 0
    connection.close()


def test_pair_enrichment_creates_audited_revision(tmp_path):
    test_paths, connection, original = initialise_with_pair(tmp_path, context=False)
    enriched = {**original,
        "context_post_id": snowflake(NOW - timedelta(days=8), 99),
        "context_posted_at": analytics.iso_utc(NOW - timedelta(days=8)),
        "context_missing_reason": None,
        "verification_label": "Exact wording",
        "discovery_sources": ["fixture", "context_history"],
    }
    result = analytics.apply_discovery(connection, [enriched], now=NOW)
    assert result["updated"] == 1
    assert connection.execute("SELECT COUNT(*) FROM post_pair_revisions").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 2
    stored = connection.execute(
        "SELECT context_post_id, context_missing_reason FROM post_pairs"
    ).fetchone()
    assert stored[0] == enriched["context_post_id"]
    assert stored[1] is None
    connection.close()


def test_rediscovery_repairs_legacy_stale_missing_context_reason(tmp_path):
    test_paths = paths(tmp_path)
    analytics.initialise_database(test_paths)
    connection = analytics.connect_database(test_paths)
    stale = pair_record(context=True)
    stale["context_missing_reason"] = "context_reply_not_recorded"
    analytics.apply_discovery(connection, [stale], now=NOW)
    corrected = {**stale, "context_missing_reason": None}

    result = analytics.apply_discovery(connection, [corrected], now=NOW)
    assert result["updated"] == 1
    assert connection.execute("SELECT context_missing_reason FROM post_pairs").fetchone()[0] is None
    assert connection.execute("SELECT COUNT(*) FROM post_pair_revisions").fetchone()[0] == 2
    connection.close()


def test_no_request_when_nothing_due_and_dry_run_requires_no_client(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path, age_days=0)
    dry = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=False, max_api_requests=2, now=NOW,
        client_factory=lambda: pytest.fail("dry-run constructed a client"),
    )
    assert dry["status"] == "nothing_due"
    assert connection.execute("SELECT COUNT(*) FROM collection_attempts").fetchone()[0] == 0
    connection.close()


def test_transient_retry_is_bounded_and_snapshot_records_retry_attempt(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    (test_paths.runtime_dir / "config.json").write_text(json.dumps({"retry_backoff_seconds": 0}))
    client = FakeClient([requests.ConnectionError("offline"), lambda ids: response_for(ids)])
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=2, now=NOW,
        client_factory=lambda: client,
    )
    assert result["requests_made"] == 2
    assert len(client.calls) == 2
    attempts = connection.execute("SELECT outcome FROM collection_attempts ORDER BY attempt_id").fetchall()
    assert [row[0] for row in attempts] == ["transport_error", "success"]
    assert {row[0] for row in connection.execute("SELECT DISTINCT request_attempt_number FROM metric_snapshots")} == {2}
    connection.close()


def test_429_persists_collector_only_cooldown_and_does_not_probe_again(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    limited = analytics.ReadResult(
        status_code=429, body={"title": "Too Many Requests"}, headers={"retry-after": "120"},
        endpoint="/2/tweets", request_id="rate", elapsed_seconds=0.1,
    )
    client = FakeClient([limited, pytest.fail])
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=2, now=NOW,
        client_factory=lambda: client,
    )
    assert result["stopped_reason"] == "rate_limited"
    assert len(client.calls) == 1
    assert analytics.state_value(connection, "cooldown_reason") == "X metrics lookup returned 429"
    assert json.loads(test_paths.state.read_text())["cooldown_until"] is not None
    connection.close()


def test_active_cooldown_prevents_client_construction(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    with connection:
        analytics.set_state_value(connection, "cooldown_until", analytics.iso_utc(NOW + timedelta(hours=1)), now=NOW)
        analytics.set_state_value(connection, "cooldown_reason", "test cooldown", now=NOW)
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=2, now=NOW,
        client_factory=lambda: pytest.fail("active cooldown constructed a client"),
    )
    assert result["status"] == "cooldown_active" and result["requests_made"] == 0
    connection.close()


def test_status_uses_configured_lateness_tolerance(tmp_path):
    test_paths = paths(tmp_path)
    analytics.initialise_database(test_paths)
    connection = analytics.connect_database(test_paths)
    record = pair_record(age_days=0)
    main_time = NOW - timedelta(hours=1, minutes=5)
    context_time = main_time + timedelta(seconds=2)
    record.update({
        "main_post_id": snowflake(main_time, 1),
        "main_posted_at": analytics.iso_utc(main_time),
        "context_post_id": snowflake(context_time, 2),
        "context_posted_at": analytics.iso_utc(context_time),
    })
    analytics.apply_discovery(connection, [record], now=NOW)
    connection.close()
    test_paths.config.write_text(json.dumps({"on_time_tolerance_seconds": 60}))

    status = analytics.database_status(test_paths, now=NOW)
    assert status["due_snapshots"] == 2
    assert status["overdue_snapshots"] == 2


def test_transient_http_failure_gets_one_retry_and_no_third_attempt(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    (test_paths.runtime_dir / "config.json").write_text(json.dumps({"retry_backoff_seconds": 0}))
    failure = analytics.ReadResult(
        status_code=503, body={"title": "Unavailable"}, headers={}, endpoint="/2/tweets",
        request_id="failed", elapsed_seconds=0.1,
    )
    client = FakeClient([failure, failure, pytest.fail])
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=3, now=NOW,
        client_factory=lambda: client,
    )
    assert len(client.calls) == 2
    assert result["stopped_reason"] == "http_503"
    assert connection.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0] == 0
    connection.close()


def test_transient_http_retry_honours_retry_after(tmp_path, monkeypatch):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    failure = analytics.ReadResult(
        status_code=503, body={"title": "Unavailable"}, headers={"retry-after": "7"},
        endpoint="/2/tweets", request_id="failed", elapsed_seconds=0.1,
    )
    client = FakeClient([failure, lambda ids: response_for(ids)])
    delays = []
    monkeypatch.setattr(analytics.time, "sleep", delays.append)
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=2, now=NOW,
        client_factory=lambda: client,
    )
    assert result["completed_snapshots"] == 10
    assert delays == [7]
    connection.close()


def test_batch_size_and_request_ceiling_leave_later_posts_pending(tmp_path):
    test_paths = paths(tmp_path)
    analytics.initialise_database(test_paths)
    connection = analytics.connect_database(test_paths)
    records = [pair_record(suffix=index) for index in range(1, 52)]
    analytics.apply_discovery(connection, records, now=NOW)
    client = FakeClient([lambda ids: response_for(ids)])
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: client,
    )
    assert len(client.calls) == 1 and len(client.calls[0]) == 100
    assert result["stopped_reason"] == "maximum_api_requests_reached"
    assert connection.execute("SELECT COUNT(*) FROM snapshot_schedule WHERE status='pending'").fetchone()[0] == 10
    connection.close()


def test_outage_leaves_schedules_pending_after_one_retry(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    (test_paths.runtime_dir / "config.json").write_text(json.dumps({"retry_backoff_seconds": 0}))
    client = FakeClient([requests.Timeout("one"), requests.Timeout("two")])
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=2, now=NOW,
        client_factory=lambda: client,
    )
    assert result["requests_made"] == 2 and result["completed_snapshots"] == 0
    assert connection.execute("SELECT COUNT(*) FROM snapshot_schedule WHERE status='pending'").fetchone()[0] == 10
    connection.close()


def test_deleted_post_is_terminal_and_missing_metrics_remain_null(tmp_path):
    test_paths, connection, record = initialise_with_pair(tmp_path, context=False)
    error = {"resource_id": record["main_post_id"], "title": "Not Found Error", "detail": "Post deleted"}
    client = FakeClient([lambda ids: response_for(ids, metrics=False, errors=[error])])
    result = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: client,
    )
    assert result["terminal_snapshots"] == 5
    row = connection.execute("SELECT * FROM metric_snapshots LIMIT 1").fetchone()
    assert row["terminal_state"] == "not_found_or_deleted"
    assert row["impressions"] is None and row["likes"] is None
    unavailable = json.loads(row["unavailable_fields_json"])
    assert unavailable["impressions"] == "not_found_or_deleted"
    connection.close()


def test_metric_extraction_uses_null_not_zero_and_denominator_rules():
    metrics, unavailable, derived = analytics.extract_metrics({
        "id": "1",
        "public_metrics": {"like_count": 0, "reply_count": 0, "retweet_count": 0, "quote_count": 0},
    })
    assert metrics["impressions"] is None
    assert metrics["likes"] == 0
    assert unavailable["impressions"].startswith("not_returned")
    assert derived["engagement_rate"] is None
    zero = analytics.derive_metrics({**metrics, "impressions": 0})
    assert zero["engagement_rate"] is None and zero["bookmark_rate"] is None


def test_x_provider_total_engagements_field_is_extracted():
    metrics, unavailable, derived = analytics.extract_metrics({
        "public_metrics": {
            "impression_count": 100,
            "like_count": 2,
            "reply_count": 1,
            "retweet_count": 1,
            "quote_count": 0,
            "bookmark_count": 1,
        },
        "non_public_metrics": {"engagements": 12},
    })
    assert metrics["provider_total_engagements"] == 12
    assert "provider_total_engagements" not in unavailable
    assert derived["engagement_count"] == 12
    assert derived["engagement_rate"] == pytest.approx(0.12)
    assert derived["engagement_rate_basis"] == "provider_total"


def test_snapshots_and_attempts_are_append_only(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    client = FakeClient([lambda ids: response_for(ids)])
    analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: client,
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("UPDATE metric_snapshots SET likes=999")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM collection_attempts")
    connection.close()


def test_preserved_raw_metric_correction_appends_audited_revision(tmp_path, monkeypatch):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    real_extract = analytics.extract_metrics

    def legacy_extract(post):
        copied = json.loads(json.dumps(post))
        (copied.get("non_public_metrics") or {}).pop("engagements", None)
        return real_extract(copied)

    def provider_response(post_ids):
        return analytics.ReadResult(
            status_code=200,
            body={"data": [{
                "id": post_id,
                "public_metrics": {
                    "impression_count": 100, "like_count": 2, "reply_count": 1,
                    "retweet_count": 1, "quote_count": 0, "bookmark_count": 1,
                },
                "non_public_metrics": {"engagements": 12},
            } for post_id in post_ids]},
            headers={}, endpoint="/2/tweets", request_id="provider-field", elapsed_seconds=0.1,
        )

    monkeypatch.setattr(analytics, "extract_metrics", legacy_extract)
    analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: FakeClient([provider_response]),
    )
    monkeypatch.setattr(analytics, "extract_metrics", real_extract)
    assert connection.execute(
        "SELECT COUNT(*) FROM metric_snapshots WHERE provider_total_engagements IS NULL"
    ).fetchone()[0] == 10

    result = analytics.revise_snapshots_from_preserved_raw(
        test_paths, connection, reason="support X non_public_metrics.engagements", now=NOW,
    )
    assert result == {"raw_files": 1, "revisions_appended": 10, "unchanged_snapshots": 0}
    assert connection.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0] == 20
    assert connection.execute(
        "SELECT COUNT(*) FROM metric_snapshots WHERE revision_number=2 AND provider_total_engagements=12"
    ).fetchone()[0] == 10
    assert connection.execute("SELECT COUNT(*) FROM metric_snapshot_revision_audit").fetchone()[0] == 10
    capabilities = analytics.state_value(connection, "metric_capabilities")
    assert capabilities["available_counts"]["provider_total_engagements"] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM metric_snapshots WHERE revision_number=1 AND provider_total_engagements IS NULL"
    ).fetchone()[0] == 10

    repeated = analytics.revise_snapshots_from_preserved_raw(
        test_paths, connection, reason="support X non_public_metrics.engagements", now=NOW,
    )
    assert repeated == {"raw_files": 1, "revisions_appended": 0, "unchanged_snapshots": 10}
    connection.close()


def test_raw_response_sanitisation_removes_secret_fields(tmp_path):
    test_paths = paths(tmp_path)
    analytics.initialise_database(test_paths)
    result = analytics.ReadResult(
        status_code=200,
        body={"data": [], "authorization": "Bearer secret", "nested": {"access_token": "secret"}},
        headers={"x-request-id": "safe"}, endpoint="/2/tweets", request_id="safe", elapsed_seconds=0.1,
    )
    relative, digest_value = analytics.write_raw_response(test_paths, result, ["123"], 1, collected_at=NOW)
    raw = (test_paths.runtime_dir / relative).read_text()
    assert "Bearer secret" not in raw and '"[REDACTED]"' in raw
    assert len(digest_value) == 64


def test_process_lock_is_non_overlapping(tmp_path):
    test_paths = paths(tmp_path)
    with analytics.collector_lock(test_paths):
        with pytest.raises(analytics.CollectorAlreadyRunning):
            with analytics.collector_lock(test_paths):
                pass
    with analytics.collector_lock(test_paths):
        pass


@pytest.mark.parametrize("inject_session", [False, True])
def test_read_client_can_only_issue_get_lookup(monkeypatch, inject_session):
    calls = []

    class Response:
        status_code = 200
        content = b"{}"
        text = "{}"
        headers = {}
        def json(self):
            return {}

    class Session:
        def __bool__(self):
            return False
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()
        def post(self, *_args, **_kwargs):
            pytest.fail("write endpoint invoked")

    session = Session()
    monkeypatch.setattr(requests, "get", session.get)
    client = analytics.XReadClient(
        base_url="https://api.x.test", timeout_seconds=10,
        **({"session": session} if inject_session else {}),
        env={"X_CONSUMER_KEY": "a", "X_CONSUMER_SECRET": "b", "X_ACCESS_TOKEN": "c", "X_ACCESS_SECRET": "d"},
    )
    assert client.session is (session if inject_session else requests)
    result = client.lookup_posts(["123"])
    assert result.status_code == 200
    assert calls[0][0] == "https://api.x.test/2/tweets"
    assert calls[0][1]["params"]["ids"] == "123"


def test_offline_reporting_and_collection_preflight_need_no_http_packages(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    connection.close()
    database_before = test_paths.database.read_bytes()
    code = """
import builtins
from datetime import datetime, timedelta
from pathlib import Path
import sys

original_import = builtins.__import__
def blocked_http_import(name, *args, **kwargs):
    if name.split('.')[0] in {'requests', 'requests_oauthlib'}:
        raise ModuleNotFoundError(f'HTTP dependency blocked by test: {name}')
    return original_import(name, *args, **kwargs)
builtins.__import__ = blocked_http_import

import mrs_engagement_analytics as analytics

now = datetime.fromisoformat(sys.argv[2])
analytics.utc_now = lambda: now
paths = analytics.AnalyticsPaths.for_project(Path(sys.argv[1]))
summary = analytics.read_digest_summary(paths.project_dir)
assert summary['available'] is True, summary
assert summary['tracked_post_pairs'] == 1
assert summary['posts_with_context_replies'] == 1

def unexpected_client():
    raise AssertionError('offline preflight constructed a client')

with analytics.connect_database(paths, readonly=True) as connection:
    assert connection.execute('PRAGMA query_only').fetchone()[0] == 1
    dry_run = analytics.collect_due_snapshots(
        paths, connection, execute_read=False, max_api_requests=2,
        now=now, client_factory=unexpected_client,
    )
    assert dry_run['status'] == 'dry_run', dry_run
    assert dry_run['due_snapshots'] == 10
    nothing_due = analytics.collect_due_snapshots(
        paths, connection, execute_read=True, max_api_requests=2,
        now=now - timedelta(days=9), client_factory=unexpected_client,
    )
    assert nothing_due['status'] == 'nothing_due', nothing_due
    assert connection.execute('SELECT COUNT(*) FROM collection_attempts').fetchone()[0] == 0

try:
    analytics.XReadClient(
        base_url='https://api.x.test', timeout_seconds=10,
        env={'X_CONSUMER_KEY': ''},
    )
except analytics.AnalyticsError as error:
    assert 'credentials are required' in str(error)
else:
    raise AssertionError('missing credentials were accepted')
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", code, str(test_paths.project_dir), NOW.isoformat()],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert test_paths.database.read_bytes() == database_before


def test_reports_warn_for_small_samples_and_digest_reads_without_network(tmp_path, monkeypatch):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    client = FakeClient([lambda ids: response_for(ids)])
    analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: client,
    )
    connection.close()
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: pytest.fail("digest made a network call"))
    summary = analytics.read_digest_summary(test_paths.project_dir, window_days=28)
    assert summary["available"] is True
    assert "context_view_ratio" in summary["sample_size_warnings"]
    with analytics.connect_database(test_paths, readonly=True) as report_connection:
        report_summary = analytics.report_summary(report_connection, window_days=28, now=NOW)
    hansard = report_summary["groups"]["source_class"]["Hansard"]
    assert hansard["context_view_ratio"]["sample_size"] == 1
    assert hansard["context_engagement_rate"]["sample_size"] == 1
    reports = analytics.generate_reports(test_paths)
    assert set(reports) == {"7", "28", "90", "None"}
    assert "observational associations" in (test_paths.reports / "trailing_28d.md").read_text()


def test_digest_backward_compatibility_without_database(tmp_path):
    project = tmp_path / "no-db"
    project.mkdir()
    before = set(project.iterdir())
    summary = analytics.read_digest_summary(project)
    assert summary["available"] is False
    assert set(project.iterdir()) == before
    report = digest.analyse([])
    report["project_dir"] = str(project)
    report["historical_context_engagement"] = summary
    rendered = digest.render_markdown(report)
    assert "## Historical context engagement" in rendered
    assert "analytics database not initialised" in rendered


def test_digest_cli_emits_structured_engagement_without_network(tmp_path, monkeypatch, capsys):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: FakeClient([lambda ids: response_for(ids)]),
    )
    connection.close()
    log_path = test_paths.project_dir / "empty.log"
    log_path.write_text("")
    json_output = tmp_path / "digest.json"
    markdown_output = tmp_path / "digest.md"
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: pytest.fail("digest made a network call"))
    monkeypatch.setattr(analytics, "utc_now", lambda: NOW)

    assert digest.main([
        str(log_path), "--project-dir", str(test_paths.project_dir), "--no-state",
        "--json-output", str(json_output), "--markdown-output", str(markdown_output),
    ]) == 0
    capsys.readouterr()
    report = json.loads(json_output.read_text())
    engagement = report["historical_context_engagement"]
    assert engagement["available"] is True
    assert engagement["tracked_post_pairs"] == 1
    assert "## Historical context engagement" in markdown_output.read_text()


def test_digest_tolerates_malformed_analytics_database(tmp_path):
    test_paths = paths(tmp_path)
    test_paths.runtime_dir.mkdir()
    test_paths.database.write_bytes(b"not a sqlite database")
    summary = analytics.read_digest_summary(test_paths.project_dir)
    assert summary["available"] is False
    assert summary["reason"].startswith("analytics database unavailable:")


def test_production_files_are_not_modified(tmp_path, monkeypatch):
    test_paths, _packets, _ids = discovery_fixture(tmp_path, monkeypatch)
    protected_names = [
        "bot_state.json", "historical_context_reply_history.json", "mrsMThatcher.log",
        "mrsMThatcher.txt", "quote_analysis.json",
    ]
    before = {name: (test_paths.project_dir / name).read_bytes() for name in protected_names}
    analytics.initialise_database(test_paths)
    pairs = analytics.discover_post_pairs(test_paths, since_days=1, now=NOW)
    with analytics.connect_database(test_paths) as connection:
        analytics.apply_discovery(connection, pairs, now=NOW)
        analytics.collect_due_snapshots(
            test_paths, connection, execute_read=False, max_api_requests=2, now=NOW,
            client_factory=lambda: pytest.fail("dry-run made a network call"),
        )
    assert before == {name: (test_paths.project_dir / name).read_bytes() for name in protected_names}


def test_cli_refuses_live_collection_without_explicit_flag(tmp_path, monkeypatch, capsys):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    connection.close()
    monkeypatch.setattr(analytics, "XReadClient", lambda **_kwargs: pytest.fail("dry-run constructed live client"))
    assert analytics.main([
        "collect", "--project-dir", str(test_paths.project_dir), "--dry-run", "--max-api-requests", "2",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry_run" and output["requests_made"] == 0


def test_bounded_backfill_requires_scope_and_confirmation(tmp_path):
    test_paths, connection, _record = initialise_with_pair(tmp_path)
    connection.close()
    with pytest.raises(SystemExit, match="date bound"):
        analytics.main([
            "backfill", "--project-dir", str(test_paths.project_dir), "--dry-run", "--max-api-requests", "1",
        ])
    with pytest.raises(SystemExit, match="confirm-read-only"):
        analytics.main([
            "backfill", "--project-dir", str(test_paths.project_dir), "--execute-read",
            "--max-pairs", "1", "--max-api-requests", "1",
        ])


@pytest.mark.parametrize("legacy_annotations", [False, True])
def test_core_analytics_preserves_existing_data_and_optional_columns(
    tmp_path, monkeypatch, legacy_annotations,
):
    test_paths, connection, record = initialise_with_pair(tmp_path, age_days=1)
    # These are retained production columns, not fields managed by the collector.
    archived = {
        "engagement_experiment_id": "substantive-question-v1",
        "engagement_experiment_plan_sha256": "a" * 64,
        "engagement_experiment_pair_id": "pair-" + "1" * 24,
        "engagement_experiment_arm": "treatment",
        "engagement_experiment_member_position": 2,
        "engagement_experiment_publication_order": "control_first",
        "engagement_experiment_sequence": 2,
        "engagement_approved_question_sha256": "b" * 64,
        "engagement_question_present": 1,
        "engagement_public_text_sha256": "c" * 64,
    }
    if legacy_annotations:
        for column, value in archived.items():
            sql_type = "INTEGER" if isinstance(value, int) else "TEXT"
            connection.execute(f"ALTER TABLE post_pairs ADD COLUMN {column} {sql_type}")
        connection.execute(
            "UPDATE post_pairs SET " + ", ".join(f"{column} = ?" for column in archived),
            tuple(archived.values()),
        )
        connection.commit()
    else:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(post_pairs)")}
        assert not columns.intersection(archived)

    first = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1, now=NOW,
        client_factory=lambda: FakeClient([lambda ids: response_for(ids)]),
    )
    assert first["completed_snapshots"] == 6
    preserved = {
        table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
        for table in ("post_pairs", "post_pair_revisions", "metric_snapshots")
    }
    connection.close()
    analytics.initialise_database(test_paths)
    connection = analytics.connect_database(test_paths)
    assert analytics.database_schema_status(connection)["schema_current"] is True
    for table, rows in preserved.items():
        assert [dict(row) for row in connection.execute(f"SELECT * FROM {table}")] == rows

    packet = {
        "quote_id": record["quote_id"], "quote_text": record["quote_text"],
        "research_confidence": "high",
    }
    monkeypatch.setattr(formatter, "load_and_validate_corpus", lambda _path: ({record["quote_id"]: packet}, set()))
    monkeypatch.setattr(formatter, "format_context_reply", lambda _packet: {})
    (test_paths.project_dir / "mrsMThatcher.txt").write_text(record["quote_text"] + "\n")
    discovered = analytics.discover_post_pairs(test_paths, now=NOW)
    assert len(discovered) == 1
    assert discovered[0]["main_post_id"] == record["main_post_id"]
    assert not set(discovered[0]).intersection(archived)
    analytics.apply_discovery(connection, discovered, now=NOW)
    stored = dict(connection.execute("SELECT * FROM post_pairs").fetchone())
    if legacy_annotations:
        assert {column: stored[column] for column in archived} == archived

    continued = analytics.collect_due_snapshots(
        test_paths, connection, execute_read=True, max_api_requests=1,
        now=NOW + timedelta(days=7),
        client_factory=lambda: FakeClient([lambda ids: response_for(ids)]),
    )
    assert continued["completed_snapshots"] == 4
    assert connection.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0] == 10
    assert [dict(row) for row in connection.execute(
        "SELECT * FROM metric_snapshots ORDER BY snapshot_id LIMIT 6"
    )] == preserved["metric_snapshots"]
    connection.close()
    assert set(analytics.generate_reports(test_paths)) == {"7", "28", "90", "None"}
    exported = analytics.export_data(test_paths, ["json", "csv", "markdown"])
    document = json.loads(Path(exported["json"]).read_text())
    assert len(document["post_pairs"]) == 1
    assert len(document["metric_snapshots"]) == 10
    if legacy_annotations:
        assert {column: document["post_pairs"][0][column] for column in archived} == archived


def test_historical_post_events_ignore_retired_optional_metadata(tmp_path, monkeypatch):
    test_paths, _packets, evidence = legacy_whitespace_hash_fixture(
        tmp_path, monkeypatch, age_days=8,
    )
    # Old metadata can be partial; only ordinary post identity still matters.
    event = {
        **evidence["event"],
        "quote_hash": evidence["raw_quote_id"],
        "engagement_experiment_arm": "treatment",
    }
    replace_fixture_log_event(evidence, event)
    before = project_durable_state(test_paths)
    pairs = analytics.discover_post_pairs(test_paths, since_days=14, now=NOW)
    assert len(pairs) == 1
    assert pairs[0]["quote_id"] == evidence["raw_quote_id"]
    assert "engagement_experiment_arm" not in pairs[0]
    assert project_durable_state(test_paths) == before


def test_scheduled_run_initialises_v1_before_discovery_or_collection(
    tmp_path, monkeypatch, capsys,
):
    test_paths = paths(tmp_path)
    test_paths.runtime_dir.mkdir()
    with sqlite3.connect(test_paths.database) as connection:
        connection.executescript(analytics.SCHEMA_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (analytics.iso_utc(NOW),),
        )
    stale = analytics.database_status(test_paths, now=NOW)
    assert stale["schema_current"] is False
    assert stale["migration_required"] is True
    assert stale["missing_columns"] == []
    monkeypatch.setattr(analytics, "configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analytics, "discover_post_pairs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(analytics, "XReadClient", lambda **_kwargs: pytest.fail("constructed an X client"))

    def local_collection(_paths, writable, **_kwargs):
        assert analytics.database_schema_status(writable)["schema_current"] is True
        return {"status": "nothing_due", "requests_made": 0, "completed_snapshots": 0}

    monkeypatch.setattr(analytics, "collect_due_snapshots", local_collection)
    assert analytics.main([
        "scheduled-run", "--project-dir", str(test_paths.project_dir),
        "--execute-read", "--max-api-requests", "1",
    ]) == 0
    capsys.readouterr()
    current = analytics.database_status(test_paths, now=NOW)
    assert current["initialised"] is True
    assert current["schema_current"] is True
    assert current["migration_required"] is False
    assert current["schema_version"] == analytics.SCHEMA_VERSION
