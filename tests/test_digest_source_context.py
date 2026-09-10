"""Source-local pending state survives interleaving, replacement and resume."""
import copy
from datetime import datetime, timedelta

import pytest

import mrs_log_digest as digest


def record(offset, message, *, selftest=False, source="log_event"):
    return digest.Record(
        datetime(2026, 9, 10, 12) + timedelta(seconds=offset),
        "INFO", source, offset + 1, message,
        "mrsMThatcher.selftest.log" if selftest else "mrsMThatcher.log",
        offset + 1,
    )


@pytest.mark.parametrize("last_selftest", [False, True], ids=["production-last", "selftest-last"])
def test_interleaved_posting_keeps_pending_references_and_event_authority(monkeypatch, last_selftest):
    values = [
        dict(label="production", line=1, quote_hash="aa", mention="101", quote="301",
             mention_reply="901", quote_reply="903", root="905", meme="907"),
        dict(label="selftest", line=2, quote_hash="bb", mention="102", quote="302",
             mention_reply="902", quote_reply="904", root="906", meme="908"),
    ]
    messages = [
        "Selected quote line_no={line} quote_hash={quote_hash} weight=1.00 seasonal_boost=False",
        "Selected line_no={line} text='{label} quote'",
        "Posting meme image: {label}.png",
        "Meme image summary for cache: '{label} meme'",
        "Considering mention id={mention} author_id=201 text='{label} mention'",
        "Considering quote tweet id={quote} author_id=401 original_post_id=501 text='{label} quote tweet'",
        "Wrote confirmed reply receipt pending local reconciliation source=mention target_id={mention} reply_post_id={mention_reply}",
        "Created X post successfully. response={{'data': {{'id': '{mention_reply}'}}}}",
        "Reply posted successfully",
        "Created X post successfully. response={{'data': {{'id': '{quote_reply}'}}}}",
        "Quote-tweet reply posted successfully",
        "Quote/image posted successfully. posted_id={root}",
        "Daily meme posted successfully. posted_id={meme} file={label}.png",
        "Removed reconciled confirmed-reply receipt",
    ]
    records = [
        record(2 * step + source, message.format(**fields), selftest=bool(source))
        for step, message in enumerate(messages)
        for source, fields in enumerate(values)
    ]
    records.append(record(len(records), "Quiet tail", selftest=last_selftest))
    pending, authority = {}, set()
    original_observe = digest.observe_error_warning
    original_enrich = digest.enrich_published_reply_text

    def observe(r, msg, **kwargs):
        pending[r.ordinal - 1] = {
            key: kwargs[key]
            for key in ("pending_quote", "pending_meme", "pending_mention", "pending_qt")
        }
        return original_observe(r, msg, **kwargs)

    def enrich(report, **kwargs):
        original_enrich(report, **kwargs)
        authority.update(kwargs["production_event_object_ids"])

    monkeypatch.setattr(digest, "observe_error_warning", observe)
    monkeypatch.setattr(digest, "enrich_published_reply_text", enrich)
    report = digest.analyse(records)
    for source, fields in enumerate(values):
        quote = pending[source]["pending_quote"]
        assert quote is pending[2 + source]["pending_quote"]
        assert quote is pending[22 + source]["pending_quote"]
        assert quote["text"] == fields["label"] + " quote"
        assert pending[24 + source]["pending_quote"] == {}
        assert pending[24 + source]["pending_quote"] is not quote

        meme = pending[6 + source]["pending_meme"]
        assert meme is pending[24 + source]["pending_meme"]
        assert meme["summary"] == fields["label"] + " meme"
        assert pending[26 + source]["pending_meme"] == {}
        assert pending[26 + source]["pending_meme"] is not meme

        mention = pending[10 + source]["pending_mention"]
        assert mention["reply_post_id"] == fields["mention_reply"]
        assert pending[18 + source]["pending_mention"] == {}
        assert pending[18 + source]["pending_mention"] is not mention
        qt = pending[12 + source]["pending_qt"]
        assert qt["reply_post_id"] == fields["quote_reply"]
        assert pending[22 + source]["pending_qt"] == {}
        assert pending[22 + source]["pending_qt"] is not qt

        expected = {
            "quote_image_posted": ("post_id", fields["root"], "text", fields["label"] + " quote"),
            "daily_meme_posted": ("post_id", fields["meme"], "summary", fields["label"] + " meme"),
            "mention_reply_posted": ("reply_post_id", fields["mention_reply"], "mention_id", fields["mention"]),
            "quote_tweet_reply_posted": ("reply_post_id", fields["quote_reply"], "quote_tweet_id", fields["quote"]),
        }
        for kind, (id_key, post_id, detail_key, detail) in expected.items():
            row, = [item for item in report["events"] if item["kind"] == kind and item[id_key] == post_id]
            assert row[detail_key] == detail
            assert (id(row) in authority) is (source == 0)
            assert report["summary"]["stats"][kind] == 2
    for key in pending[0]:
        assert pending[0][key] is not pending[1][key]
    receipts = report["confirmed_reply_recovery"]["receipt_events"]
    assert [(row["kind"], row["source_class"], row["target_id"], row["reply_post_id"]) for row in receipts] == [
        ("written", "production", "101", "901"),
        ("written", "selftest", "102", "902"),
        ("removed", "production", "101", "901"),
        ("removed", "selftest", "102", "902"),
    ]
    assert report["resume_context"] == dict(
        active_xai_context=None, active_xai_call_attempt=None, pending_mention=None, pending_qt=None,
    )


@pytest.mark.parametrize("last_selftest", [False, True], ids=["production-last", "selftest-last"])
def test_resume_keeps_production_provider_attempt_and_both_pending_lanes(monkeypatch, last_selftest):
    prefix = [
        record(0, "Considering mention id=101 author_id=201 text='production mention'"),
        record(1, "Considering mention id=102 author_id=202 text='selftest mention'", selftest=True),
        record(2, "Considering quote tweet id=301 author_id=401 original_post_id=501 text='production quote'"),
        record(3, "Considering quote tweet id=302 author_id=402 original_post_id=502 text='selftest quote'", selftest=True),
        record(4, "Calling AI-first reply stage=proposer model=production-model", source="xai_structured_reply_call"),
        record(5, "Calling AI-first reply stage=proposer model=selftest-model", selftest=True, source="xai_structured_reply_call"),
        record(6, "Quiet tail", selftest=last_selftest),
    ]
    observations = []
    original = digest.observe_provider_message

    def observe(r, msg, **kwargs):
        context, index = original(r, msg, **kwargs)
        observations.append((kwargs, context, index))
        return context, index

    monkeypatch.setattr(digest, "observe_provider_message", observe)
    first = digest.analyse(prefix)
    resume = first["resume_context"]
    assert set(resume) == {"active_xai_context", "active_xai_call_attempt", "pending_mention", "pending_qt"}
    assert resume["pending_mention"]["mention_id"] == "101"
    assert resume["pending_qt"]["quote_tweet_id"] == "301"
    assert resume["active_xai_context"] is observations[4][1]
    assert resume["active_xai_context"] == dict(lane="quote-tweet", context_id="301", author_id="401")
    attempts = observations[0][0]["xai_call_attempts"]
    assert resume["active_xai_call_attempt"] == attempts[0]
    assert resume["active_xai_call_attempt"] is not attempts[0]
    assert [attempt["model"] for attempt in attempts] == ["production-model", "selftest-model"]
    assert all(not attempt["usage_observed"] for attempt in attempts)
    assert resume["pending_mention"] is not observations[4][0]["pending_mention"]
    assert resume["pending_qt"] is not observations[4][0]["pending_qt"]
    assert all(not key.startswith("_") for name in ("pending_mention", "pending_qt") for key in resume[name])

    saved = copy.deepcopy(resume)
    observations.clear()
    suffix = [
        record(7, "xAI reply stage=proposer usage={}", selftest=True, source="usage_logger"),
        record(8, "xAI reply stage=proposer usage={}", source="usage_logger"),
        record(9, "Calling AI-first reply stage=proposer model=new-selftest-model", selftest=True, source="xai_structured_reply_call"),
        record(10, "Quiet tail", selftest=last_selftest),
    ]
    resumed = digest.analyse(suffix, **{"initial_" + key: value for key, value in resume.items()})
    assert resume == saved
    assert observations[0][0]["pending_mention"] == observations[0][0]["pending_qt"] == {}
    assert observations[0][0]["active_xai_context"] is None
    assert observations[0][0]["active_xai_call_attempt_index"] is None
    production = observations[1][0]
    assert production["active_xai_context"] == resume["active_xai_context"]
    assert production["active_xai_context"] is not resume["active_xai_context"]
    assert production["active_xai_call_attempt_index"] == 0
    assert observations[1][2] is None
    assert observations[2][2] == 1
    assert [row["call_start_matched"] for row in production["xai_usage_events"]] == [False, True]
    assert [row["context_id"] for row in production["xai_usage_events"]] == ["", "301"]
    assert [row["model"] for row in production["xai_usage_events"]] == ["", "production-model"]
    assert [row["usage_observed"] for row in production["xai_call_attempts"]] == [True, False]
    assert resumed["summary"]["stats"]["xai_usage_successes"] == 2
    assert resumed["resume_context"] == {**resume, "active_xai_call_attempt": None}


def test_empty_analysis_preserves_resume_shallow_copies_and_private_markers():
    shared = []
    mention = {"mention_id": "101", "shared": shared, "_identity_production": False}
    qt = {"quote_tweet_id": "301", "shared": shared}
    context = {"lane": "mention", "context_id": "101", "shared": shared}
    before = copy.deepcopy((mention, qt, context))
    report = digest.analyse(
        [], initial_pending_mention=mention, initial_pending_qt=qt,
        initial_active_xai_context=context,
    )
    resume = report["resume_context"]
    assert (mention, qt, context) == before
    for key, initial in (("pending_mention", mention), ("pending_qt", qt), ("active_xai_context", context)):
        assert resume[key] is not initial
        assert resume[key]["shared"] is shared
    assert "_identity_production" not in resume["pending_mention"]
    assert "_reply_post_id_production" not in resume["pending_mention"]
    assert "_identity_production" not in resume["pending_qt"]
    assert resume["active_xai_call_attempt"] is None
