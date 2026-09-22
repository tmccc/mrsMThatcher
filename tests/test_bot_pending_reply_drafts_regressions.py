"""Regression tests for bot pending reply drafts."""

from __future__ import annotations

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.reply_evaluation import legacy_reply_evaluator

import copy
import json
import hashlib
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import mrs_log_digest as digest
import mrs_provider_request_records as request_records
import single_call_reply as pipeline
from mrs_bot_reply_generation import log_ai_reply_posting_outcome
from mrs_bot_reply_model_transport import ReplyModelTransport
from mrs_provider_request_records import read_provider_request_record
from reply_evidence import EvidenceRepository
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime
from tests.helpers.reply_fixtures import (
    patch_reply_owner_method,
    UNIT_REPLY_REPOSITORY,
    UnitReplyEvidenceRepository,
    unit_reply_context,
    unit_approved_reply,
    unit_confirmed_reply_receipt,
)
from single_call_reply import ValidatedReply
from tests.helpers.single_call_fixtures import (
    enabled_config,
    raw_decision,
    response_envelope,
)
from tools import extract_prospective_conversations as extractor


pytestmark = pytest.mark.allow_loopback_network


def test_capture_identity_survives_draft_receipt_and_reconciliation_round_trips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery and confirmed reconciliation retain one exact provider call."""

    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    class ConnectTimeout(Timeout):
        pass

    class ConnectionError(RequestException):
        pass

    text = "Responsibility matters more than rhetoric."
    response = SimpleNamespace(
        status_code=200,
        headers={},
        json=lambda: response_envelope(raw_decision(reply=text)),
        close=lambda: None,
    )
    sent_bodies: list[bytes] = []

    def post(*_args: object, **kwargs: object) -> object:
        sent_bodies.append(bytes(kwargs["data"]))
        return response

    ticks = iter((1.0, 1.01))
    transport = ReplyModelTransport(
        log=Mock(),
        model=pipeline.MODEL,
        reasoning_effort=pipeline.REASONING_EFFORT,
        monotonic=lambda: next(ticks),
        require_remote_operation_unpaused=lambda _operation: None,
        report_bot_health_progress=lambda _boundary: None,
        requests=SimpleNamespace(
            post=post,
            RequestException=RequestException,
            Timeout=Timeout,
            ConnectTimeout=ConnectTimeout,
            ConnectionError=ConnectionError,
        ),
        base_url="https://fixture.invalid/v1",
        api_key="fixture-secret",
        sleep=lambda _seconds: None,
        now_epoch=lambda: 0,
        error_type=RuntimeError,
        request_record_directory=tmp_path / "ai-request-records",
    )
    context = unit_reply_context(target_id="100")
    result = pipeline.run_reply_pipeline(
        context=context,
        config=enabled_config(),
        repository=UNIT_REPLY_REPOSITORY,
        transport=transport.call,
    )

    assert result.status == "reply" and result.reply is not None
    assert result.call_id and result.reply.draft_record["call_id"] == result.call_id
    capture = read_provider_request_record(
        tmp_path / "ai-request-records" / f"{result.call_id}.json.gz"
    )
    assert capture["request_body_utf8"].encode("utf-8") == sent_bodies[0]

    monkeypatch.setattr(
        bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY,
    )
    state = bot.default_state()
    first_owner = bot._reply_draft_owner()
    assert first_owner.store(
        state, "100", "mention", result.reply, context=context,
    )
    round_tripped = json.loads(json.dumps(state))
    recovery_events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: recovery_events.append((name, fields)),
    )
    recovered = bot._reply_draft_owner().recover(
        round_tripped, "100", "mention", context=context,
    )

    assert recovered is not None and recovered.reply is not None
    assert recovered.model_call_count == recovered.provider_request_attempt_count == 0
    assert recovered.call_id == result.call_id
    assert recovered.reply.draft_record["call_id"] == result.call_id
    assert recovered.reply.pipeline_metadata["call_id"] == result.call_id
    recovered_event = next(
        fields for name, fields in recovery_events
        if name == "single_call_reply_draft_recovered"
    )
    assert recovered_event["call_id"] == result.call_id

    posting_events: list[tuple[str, dict[str, object]]] = []
    log_ai_reply_posting_outcome(
        reply=recovered.reply,
        status="confirmed",
        lane="mention",
        target_id="100",
        reply_post_id="999",
        failure_reason="",
        log_event=lambda name, **fields: posting_events.append((name, fields)),
    )
    assert posting_events[0][1]["call_id"] == result.call_id

    receipt = unit_confirmed_reply_receipt(target_id="100", text=text)
    receipt["reply_context"] = copy.deepcopy(context)
    receipt["ai_reply_draft"] = copy.deepcopy(recovered.reply.draft_record)
    receipt = json.loads(json.dumps(receipt))
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt)
    reconciliation_events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: reconciliation_events.append((name, fields)),
    )
    bot.apply_confirmed_reply_receipt(bot.default_state(), receipt)
    confirmed = next(
        fields for name, fields in reconciliation_events
        if name == "single_call_reply_posting_outcome"
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["call_id"] == result.call_id
    assert len(sent_bodies) == 1


def test_duplicate_recovery_retains_historical_capture_across_digest_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected recovered draft keeps its validated creation-time call ID."""

    class RequestException(Exception):
        pass

    class Timeout(RequestException):
        pass

    class ConnectTimeout(Timeout):
        pass

    class ConnectionError(RequestException):
        pass

    text = "Responsibility matters more than rhetoric."
    response = SimpleNamespace(
        status_code=200,
        headers={},
        json=lambda: response_envelope(raw_decision(reply=text)),
        close=lambda: None,
    )
    sent_bodies: list[bytes] = []
    transport_events: list[tuple[str, dict[str, object]]] = []

    def post(*_args: object, **kwargs: object) -> object:
        sent_bodies.append(bytes(kwargs["data"]))
        return response

    monkeypatch.setattr(
        request_records,
        "utc_now_text",
        lambda: "2026-09-03T10:00:00Z",
    )
    ticks = iter((1.0, 1.01))
    transport = ReplyModelTransport(
        log=Mock(),
        model=pipeline.MODEL,
        reasoning_effort=pipeline.REASONING_EFFORT,
        monotonic=lambda: next(ticks),
        require_remote_operation_unpaused=lambda _operation: None,
        report_bot_health_progress=lambda _boundary: None,
        requests=SimpleNamespace(
            post=post,
            RequestException=RequestException,
            Timeout=Timeout,
            ConnectTimeout=ConnectTimeout,
            ConnectionError=ConnectionError,
        ),
        base_url="https://fixture.invalid/v1",
        api_key="fixture-secret",
        sleep=lambda _seconds: None,
        now_epoch=lambda: 0,
        error_type=RuntimeError,
        request_record_directory=tmp_path / "ai-request-records",
        log_event=lambda name, **fields: transport_events.append((name, fields)),
    )
    context = unit_reply_context(target_id="100")
    generated = pipeline.run_reply_pipeline(
        context=context,
        config=enabled_config(),
        repository=UNIT_REPLY_REPOSITORY,
        transport=transport.call,
    )

    assert generated.status == "reply" and generated.reply is not None
    assert generated.call_id
    capture_path = (
        tmp_path / "ai-request-records" / f"{generated.call_id}.json.gz"
    )
    capture = read_provider_request_record(capture_path)
    assert capture["captured_at"] == "2026-09-03T10:00:00Z"
    assert capture["request_body_utf8"].encode("utf-8") == sent_bodies[0]
    prepared = next(
        fields for name, fields in transport_events
        if name == "provider_request_prepared"
    )
    assert prepared["call_id"] == generated.call_id
    assert prepared["captured_at"] == capture["captured_at"]
    capture_mtime = datetime(2026, 9, 3, 10).timestamp()
    os.utime(capture_path, (capture_mtime, capture_mtime))

    monkeypatch.setattr(
        bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    state = bot.default_state()
    assert bot._reply_draft_owner().store(
        state, "100", "mention", generated.reply, context=context,
    )
    stored = state["pending_ai_reply_drafts"]["mention:100"]
    assert stored["call_id"] == generated.call_id
    state["ai_reply_history"] = [{
        "target_id": "90",
        "reply_post_id": "900",
        "candidate_source": "mention",
        "reply_epoch": 1_999_999_999,
        "proposed_reply": text,
    }]
    round_tripped = json.loads(json.dumps(state))
    decision_events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: decision_events.append((name, fields)),
    )
    recovery_owner = bot._reply_draft_owner()
    recovered = recovery_owner.recover(
        round_tripped,
        "100",
        "mention",
        context=context,
        recent_replies=recovery_owner.history.recovery_replies(
            round_tripped, context=context,
        ),
    )

    assert recovered is not None
    assert recovered.status == "operational_failure"
    assert recovered.reason == "persisted_draft_local_validation_failed"
    assert recovered.error_category == "local_validation"
    assert recovered.validation_error_codes == ("exact_duplicate_reply",)
    assert recovered.call_id == generated.call_id
    assert recovered.reply is None
    assert recovered.model_call_count == 0
    assert recovered.provider_request_attempt_count == 0
    assert round_tripped.get("pending_ai_reply_drafts") is None
    assert len(sent_bodies) == 1

    assert len(decision_events) == 1
    event_name, event_fields = decision_events[0]
    assert event_name == "single_call_reply_decision"
    assert event_fields["call_id"] == generated.call_id
    assert event_fields["model_call_count"] == 0
    assert event_fields["provider_request_attempt_count"] == 0

    later = datetime(2026, 9, 4, 12)
    record = digest.Record(
        later,
        "INFO",
        "log_event",
        1,
        "EVENT " + json.dumps({"event": event_name, **event_fields}),
        "mrsMThatcher.log",
        1,
    )
    report = digest.analyse([record])
    correlations = report["provider_request_correlations"]
    assert len(correlations) == 1
    parsed_decision = correlations[0]
    assert parsed_decision["call_id"] == generated.call_id
    assert parsed_decision["model_call_count"] == 0
    assert parsed_decision["provider_request_attempt_count"] == 0
    exported, coverage = digest.provider_request_export(
        tmp_path / "ai-request-records",
        correlations,
        window_start=later,
        window_end=later,
    )
    assert len(exported) == 1
    assert exported[0]["call_id"] == generated.call_id
    assert exported[0]["capture_status"] == "complete"
    recovered_body = exported[0]["request_body_utf8"].encode("utf-8")
    assert recovered_body == sent_bodies[0]
    assert exported[0]["request_body_sha256"] == hashlib.sha256(
        sent_bodies[0]
    ).hexdigest()
    assert coverage["logical_call_denominator"] == 1
    assert coverage["physical_attempt_denominator"] == 0

    line = (
        "2026-09-04 12:00:00 INFO mrsMThatcher2.test:1 - EVENT "
        + json.dumps(
            {"event": event_name, **event_fields},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    parsed, warnings = extractor.parse_log_records(line.encode("utf-8"))
    statistics: dict[str, object] = {}
    posts = extractor.normalise_canonical_posts(
        parsed, [], b"t" * 32, parser_statistics=statistics,
    )
    assert warnings == []
    assert statistics["registered_event_missing_target_count"] == 0
    assert posts[0]["provider_call_ids"] == [generated.call_id]

    capture_path.unlink()
    missing, missing_coverage = digest.provider_request_export(
        tmp_path / "ai-request-records",
        correlations,
        window_start=later,
        window_end=later,
    )
    assert missing == [{
        "call_id": generated.call_id,
        "capture_status": "missing_expected_record",
        "error": (
            "provider request record is unavailable: "
            f"{generated.call_id}.json.gz"
        ),
    }]
    assert missing_coverage["physical_attempt_denominator"] == 0
    assert recovered.reply is None
    assert len(sent_bodies) == 1


def test_tampered_draft_call_id_is_not_trusted_as_failure_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call ID changed outside the draft hash remains ordinary invalid data."""

    context = unit_reply_context(target_id="100")
    record = copy.deepcopy(unit_approved_reply(context).draft_record)
    record.pop("validated_draft_hash")
    record["call_id"] = "11111111-2222-4333-8444-555555555555"
    record["validated_draft_hash"] = pipeline.value_sha256(record)
    record["call_id"] = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    state = {"pending_ai_reply_drafts": {"mention:100": record}}
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY,
    )
    monkeypatch.setattr(
        bot, "log_event", lambda name, **fields: events.append((name, fields)),
    )

    recovered = bot._reply_draft_owner().recover(
        state,
        "100",
        "mention",
        context=context,
        recent_replies=[{"post_id": "900", "text": str(record["proposed_reply"])}],
    )

    assert recovered is not None
    assert recovered.status == "draft_discarded"
    assert recovered.reason == "obsolete_or_invalid_persisted_draft"
    assert recovered.call_id is None
    assert state == {}
    assert events == []


def test_legacy_pending_draft_recovers_without_inventing_capture_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-correlation schema-4 shape retains its hash and safe recovery."""

    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(context)
    assert "call_id" not in reply.draft_record
    state = bot.default_state()
    monkeypatch.setattr(
        bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY,
    )
    assert bot._reply_draft_owner().store(
        state, "100", "mention", reply, context=context,
    )
    recovered = bot._reply_draft_owner().recover(
        json.loads(json.dumps(state)), "100", "mention", context=context,
    )

    assert recovered is not None and recovered.reply is not None
    assert recovered.call_id is None
    assert recovered.reply.pipeline_metadata.get("call_id") is None
    assert "call_id" not in recovered.reply.draft_record


def test_pending_ai_reply_survives_state_round_trip_and_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="A stable contribution.")
    reply = unit_approved_reply(context, text="The first draft remains the first draft.")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    bot.save_state(state, durable=True)
    loaded = bot.load_state()
    reused = bot.pending_ai_reply(loaded, "100", "mention", context=context)
    assert reused == reply
    assert isinstance(reused, ValidatedReply)
    assert reused.draft_record == reply.draft_record


@pytest.mark.parametrize("whitespace", ["  ", "\t", "\n"], ids=["spaces", "tab", "newline"])
def test_real_factual_passage_whitespace_survives_pending_draft_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    whitespace: str,
) -> None:
    """Reuse canonical fact prose while keeping raw evidence and claims bound."""

    project_root = Path(__file__).resolve().parents[1]
    document = json.loads(
        (project_root / "reply_factual_evidence.json").read_text(encoding="utf-8")
    )
    record = next(
        row for row in document["records"]
        if row["fact_id"] == "berlin-wall-east-berliners-cross-west-1989"
    )
    canonical_passage = record["passage"]
    record["passage"] = canonical_passage.replace("After new", f"After{whitespace}new")
    evidence_path = tmp_path / "reply_factual_evidence.json"
    evidence_path.write_text(json.dumps(document), encoding="utf-8")
    research_dir = project_root / "semantic_alignment_research" / "quote_research_full_001"
    repository = EvidenceRepository(research_dir, factual_evidence_path=evidence_path)
    passage = next(
        source for source in repository.passages.values()
        if source.passage == record["passage"]
    )
    context = unit_reply_context(
        target_id="100",
        contribution="How did East Berliners cross to the West when the Berlin Wall fell?",
    )
    payload, fact_map = pipeline.build_model_payload(context=context, repository=repository)
    fact_id = next(
        row["id"] for row in payload["trusted_facts"]
        if row["passage"] == canonical_passage
    )
    assert fact_map[fact_id]["source_identity"] == passage.evidence_id
    assert fact_map[fact_id]["source_record_sha256"] == pipeline.value_sha256(
        passage.prompt_record()
    )
    transport = Mock(return_value={"response": response_envelope(raw_decision(
        kind="direct_factual", reply=canonical_passage, facts=[fact_id],
    ))})
    result = pipeline.run_reply_pipeline(
        context=context,
        config=enabled_config(),
        repository=repository,
        transport=transport,
    )
    assert result.status == "reply"
    assert result.reply is not None
    reply = result.reply
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: repository)
    state = bot.default_state()
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context)
    assert pipeline.validate_persisted_draft(
        reply.draft_record, context=context, repository=repository,
    ) == reply.draft_record
    bot.save_state(state, durable=True)
    loaded = bot.load_state()
    reused = bot.pending_ai_reply(loaded, "100", "mention", context=context)
    assert isinstance(reused, ValidatedReply)
    assert reused == canonical_passage
    assert reused.draft_record == reply.draft_record
    assert transport.call_count == 1

    changed_claim = copy.deepcopy(reply.draft_record)
    changed_claim["proposed_reply"] = canonical_passage.replace("seven", "eight")
    changed_claim["factual_claims"][0]["text"] = changed_claim["proposed_reply"]
    changed_claim.pop("validated_draft_hash")
    changed_claim["validated_draft_hash"] = pipeline.value_sha256(changed_claim)
    with pytest.raises(pipeline.ReplyValidationError, match="unsupported_factual_claim"):
        pipeline.validate_persisted_draft(
            changed_claim, context=context, repository=repository,
        )

    # Even whitespace-only evidence changes must retain the raw-record binding.
    record["passage"] = canonical_passage
    evidence_path.write_text(json.dumps(document), encoding="utf-8")
    changed_repository = EvidenceRepository(research_dir, factual_evidence_path=evidence_path)
    with pytest.raises(ValueError, match="source record changed"):
        pipeline.validate_persisted_draft(
            reply.draft_record, context=context, repository=changed_repository,
        )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: changed_repository)
    assert bot.pending_ai_reply(loaded, "100", "mention", context=context) is None
    assert not bot.store_pending_ai_reply(
        bot.default_state(), "100", "mention", reply, context=context,
    )
    assert transport.call_count == 1


def test_confirmed_reply_reconciliation_clears_pending_ai_draft() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(context, text="A stable draft.")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        text="A stable draft.",
    )
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_nonempty_v1_pending_reply_draft_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "pending_reply_drafts": {
            "mention:100": {
                "target_id": "different", "candidate_source": "mention",
                "reply_text": "Unsafe stale draft.",
                "strategy_metadata": {"mode": "wry_reply", "reply_text": "Unsafe stale draft."},
            }
        }
    }
    state_file = tmp_path / "bot_state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    with pytest.raises(RuntimeError, match="Legacy V1 reply drafts remain"):
        bot.load_state()


def test_pending_ai_reply_rejects_changed_incoming_context() -> None:
    state = bot.default_state()
    original = unit_reply_context(target_id="100", contribution="A first contribution.")
    changed = unit_reply_context(target_id="100", contribution="A materially different contribution.")
    reply = unit_approved_reply(original, text="Responsibility matters.", mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=original) is True

    assert bot.pending_ai_reply(state, "100", "mention", context=changed) is None


def test_safe_pending_opinion_reply_reuses_the_persisted_context() -> None:
    state = bot.default_state()
    incoming = "Institutions endure when people defend their purpose."
    text = "Responsibility matters more than rhetoric."
    context = unit_reply_context(target_id="100", contribution=incoming)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    assert bot.pending_ai_reply(state, "100", "mention", context=context) == reply


def test_pending_ai_reply_is_retired_if_confirmed_replies_now_duplicate_it() -> None:
    state = bot.default_state()
    incoming = "Institutions endure when people defend their purpose."
    text = "Responsibility matters more than rhetoric."
    context = unit_reply_context(target_id="100", contribution=incoming)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    outcome: dict[str, object] = {}
    reused = bot.pending_ai_reply(
        state,
        "100",
        "mention",
        context=context,
        recent_replies=[text],
        evaluation_outcome=outcome,
    )
    assert reused is None
    assert state.get("pending_ai_reply_drafts") is None
    assert outcome == {
        "status": "operational_failure",
        "reason": "persisted_draft_local_validation_failed",
        "error_category": "local_validation",
        "model_call_count": 0,
    }


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply", "quote_tweet"])
def test_fresh_draft_checks_current_history_without_changing_model_context(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    """Queued posts retain chronological context but cannot repeat newer replies."""

    current = 2_000_000_000
    text = "Responsibility matters more than rhetoric."
    context = unit_reply_context(target_id="102", lane=lane)
    state = bot.default_state()
    state["ai_reply_history"] = [{
        "target_id": "101",
        "reply_post_id": "9000",
        "candidate_source": "mention",
        "reply_epoch": current - 1,
        "proposed_reply": text,
    }]
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    same_author, recent = bot._reply_history_owner().for_evaluation(
        state, context=context, target_id="102",
    )
    assert same_author == [] and recent == []
    transport = Mock(return_value={"response": response_envelope(raw_decision(
        kind="principle", reply=text,
    ))})
    result = pipeline.run_reply_pipeline(
        context=context,
        config=enabled_config(),
        repository=UNIT_REPLY_REPOSITORY,
        transport=transport,
        same_author_interactions=same_author,
        recent_account_replies=recent,
    )
    assert result.status == "reply"
    assert result.reply == text
    before = copy.deepcopy(state)

    assert not bot.store_pending_ai_reply(
        state, "102", lane, result.reply, context=context,
    )

    assert state == before
    assert transport.call_count == 1


def test_duplicate_pending_draft_is_retired_and_later_mention_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery performs no paid retry and cannot starve a later candidate."""

    current = 2_000_000_000
    text = "Responsibility matters more than rhetoric."
    candidates = [
        {
            "id": target_id,
            "author_id": author_id,
            "conversation_id": target_id,
            "text": contribution,
            "referenced_tweets": [],
            "entities": {
                "mentions": [
                    {"id": str(bot.MY_USER_ID), "username": "MrsMThatcher"}
                ]
            },
        }
        for target_id, author_id, contribution in (
            ("101", "201", "@MrsMThatcher A first contribution."),
            ("102", "202", "@MrsMThatcher A later contribution."),
        )
    ]

    def candidate_context(candidate: dict) -> dict[str, object]:
        return unit_reply_context(
            target_id=str(candidate["id"]),
            contribution=str(candidate["text"]),
            target_author_id=str(candidate["author_id"]),
        )

    state = bot.default_state()
    first_context = candidate_context(candidates[0])
    reply = unit_approved_reply(
        first_context,
        text=text,
        mode="opinion_or_principle",
    )
    assert bot.store_pending_ai_reply(
        state,
        "101",
        "mention",
        reply,
        context=first_context,
    ) is True
    state["ai_reply_history"] = [
        {
            "target_id": "90",
            "reply_post_id": "9000",
            "candidate_source": "mention",
            "reply_epoch": current - 1,
            "proposed_reply": text,
        }
    ]
    state["daily_reply_date"] = bot.reply_cap_date_str(current)
    state["daily_reply_count"] = 2
    calls: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    def decide(
        context: dict[str, object],
        *_args: object,
        evaluation_outcome: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> None:
        calls.append(str(context["target_id"]))
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "no_reply",
                "reason": "completed_exchange",
                "reason_code": "completed_exchange",
                "model_call_count": 1,
            }
        )
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(current),
    )
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot._mention_discovery, "get_mentions",
        lambda _state, **_kwargs: copy.deepcopy(candidates),
    )
    monkeypatch.setattr(
        bot._hot_post_discovery, "get_hot_post_reply_candidates",
        lambda _state, **_kwargs: [],
    )
    monkeypatch.setattr(
        bot,
        "is_probably_spam_or_not_worth_replying",
        lambda _text: False,
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda candidate, _state: PreparedReplyContext(candidate_context(candidate), {}),
    )
    monkeypatch.setattr(
        bot,
        "reply_media_context_for_candidate",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: UNIT_REPLY_REPOSITORY,
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(decide),
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert calls == ["102"]
    assert state.get("pending_ai_reply_drafts") is None
    assert bot.terminal_reply_evaluation(state, "101")["outcome"] == (
        "operational_failure"
    )
    assert bot.terminal_reply_evaluation(state, "102")["outcome"] == "no_reply"
    assert state["daily_reply_count"] == 2
    assert state["author_evaluation_quarantines"] == {}
    recovery_events = [
        values
        for name, values in events
        if name == "single_call_reply_decision"
        and values.get("target_id") == "101"
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0]["model_call_count"] == 0
    assert recovery_events[0]["error_category"] == "local_validation"

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert calls == ["102"]


def test_recovery_duplicate_comparisons_include_same_author_beyond_latest_30(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not lose an author's older confirmed prose behind global volume."""

    current = 2_000_000_000
    same_author_text = "An older same-author confirmed reply."
    same_author_contribution = "An older contribution from this author."
    state = bot.default_state()
    state["ai_reply_history"] = [
        {
            "target_id": "50",
            "reply_post_id": "8000",
            "author_id": "201",
            "conversation_id": "50",
            "root_post_id": "50",
            "incoming_contribution": same_author_contribution,
            "incoming_contribution_sha256": hashlib.sha256(
                same_author_contribution.encode("utf-8")
            ).hexdigest(),
            "candidate_source": "mention",
            "reply_epoch": current - 100,
            "proposed_reply": same_author_text,
        },
        *[
            {
                "target_id": str(100 + index),
                "reply_post_id": str(9000 + index),
                "candidate_source": "hot_post_reply",
                "reply_epoch": current - 40 + index,
                "proposed_reply": f"Newer global reply {index}.",
            }
            for index in range(31)
        ],
    ]
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    context = unit_reply_context(
        target_id="500",
        target_author_id="201",
    )

    comparisons = bot.recovery_comparison_account_replies(
        state,
        context=context,
    )

    assert len(comparisons) == 31
    assert comparisons[0] == {
        "post_id": "8000",
        "text": same_author_text,
    }
    assert [row["text"] for row in comparisons[1:]] == [
        f"Newer global reply {index}." for index in range(1, 31)
    ]


def test_pending_ai_reply_rejects_overlong_incoming_context() -> None:
    incoming = (
        "Institutions endure when people defend their purpose. "
        + ("context " * 3000).strip()
    )
    text = "Responsibility matters more than rhetoric."
    context = unit_reply_context(target_id="100", contribution=incoming)

    assert len(incoming) > 10_000
    with pytest.raises(RuntimeError, match="visible-context character bound"):
        unit_approved_reply(context, text=text, mode="opinion_or_principle")


def test_pending_ai_reply_rejects_context_beyond_schema_limit() -> None:
    text = "Responsibility matters more than rhetoric."
    context = unit_reply_context(target_id="100", contribution="x" * 20_001)

    with pytest.raises(RuntimeError, match="visible-context character bound"):
        unit_approved_reply(context, text=text, mode="opinion_or_principle")


def test_pending_reply_created_under_an_older_strategy_version_is_not_reused() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(context, text="An old draft.")
    record = dict(reply.draft_record)
    record["strategy_version"] = "ai-first-reply-v1"
    state["pending_ai_reply_drafts"] = {"mention:100": record}

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_factual_reply_is_not_reused_when_evidence_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    missing_repository = UnitReplyEvidenceRepository()
    missing_repository.passages = {}
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: missing_repository)

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_factual_reply_fails_closed_when_local_corpus_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid local corpus")),
    )

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_reply_is_preserved_when_evidence_repository_is_temporarily_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(
        context,
        text="Responsibility matters more than rhetoric.",
        mode="opinion_or_principle",
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context)
    saved = copy.deepcopy(state["pending_ai_reply_drafts"]["mention:100"])
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: (_ for _ in ()).throw(bot.ReplyEvidenceUnavailable("corpus unavailable")),
    )

    with pytest.raises(bot.ReplyEvidenceUnavailable, match="corpus unavailable"):
        bot.pending_ai_reply(state, "100", "mention", context=context)

    assert state["pending_ai_reply_drafts"]["mention:100"] == saved


def test_pending_factual_reply_is_reused_after_source_hash_revalidation() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    reused = bot.pending_ai_reply(state, "100", "mention", context=context)

    assert reused == reply
    assert reused.draft_record["used_fact_sources"] == reply.draft_record["used_fact_sources"]


def test_pending_ai_reply_drafts_are_bounded() -> None:
    state = bot.default_state()
    for target in range(101, 202):
        context = unit_reply_context(target_id=str(target))
        reply = unit_approved_reply(context, text="Stable draft.")
        assert bot.store_pending_ai_reply(state, str(target), "mention", reply, context=context)
    assert len(state["pending_ai_reply_drafts"]) == 100
    assert "mention:101" not in state["pending_ai_reply_drafts"]
    assert "mention:201" in state["pending_ai_reply_drafts"]
