from __future__ import annotations

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.reply_evaluation import legacy_reply_evaluator

import base64
import hashlib
import json
import logging
import signal
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers.quote_candidate_overrides import patch_completed_research_quotes

import historical_context_outbox as outbox_module
import mrsMThatcher2 as bot
import remote_media_upload_receipt as media_receipt_module
import remote_write_transport_journal as journal_module
import x_api_error_semantics as error_semantics
from historical_context_formatter import (
    AmbiguousContextReplyOutcome,
    HistoricalContextReplyStore,
)
from tests.fake_api_server import FakeApiServer
from tests.helpers.reply_fixtures import (
    patch_reply_owner_method,
    patch_tweet_lookup_method,
    UNIT_REPLY_REPOSITORY,
    unit_approved_reply,
    unit_reply_context,
    unit_sending_reply_receipt,
    unit_sending_v4_reply_receipt,
)
from tests.helpers.bot_fixtures import configure_simple_meme_post, configure_simple_quote_post

from tests.helpers.x_response_fixtures import (
    _x_response,
    PRODUCTION_DELETED_REPLY_ERROR,
    _existing_reply_target_then_deleted_create,
    _raw_x_response,
    _armed_x_create_transaction,
    _armed_x_create_authority,
    isolate_remote_write_state,
)


PRODUCTION_BLANK_CREATE_RESPONSE_BODY = (
    b'{"data":{"edit_history_tweet_ids":[],"id":"","text":""}}'
)


def _x_create_anomaly_records(
    caplog: pytest.LogCaptureFixture,
) -> list[tuple[str, dict[str, object]]]:
    prefix = f"{bot.X_CREATE_RESPONSE_ANOMALY_EVENT} "
    records: list[tuple[str, dict[str, object]]] = []
    for record in caplog.records:
        message = record.getMessage()
        if record.levelno == logging.ERROR and message.startswith(prefix):
            records.append((message, json.loads(message[len(prefix) :])))
    return records


def _recompute_x_create_diagnostic_sha256(event: dict[str, object]) -> str:
    without_hash = dict(event)
    without_hash.pop("diagnostic_sha256")
    canonical = json.dumps(
        without_hash,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize("transport", ("tweet", "media"))
def test_uninspectable_context_receipt_namespace_blocks_final_transport(
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
) -> None:
    """A final receipt inspection error is never interpreted as absence."""

    real_lstat = bot.os.lstat

    def deny_context_receipt(path, *args, **kwargs):
        if Path(path) == bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE:
            raise PermissionError("injected context receipt inspection failure")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(bot.os, "lstat", deny_context_receipt)
    if transport == "tweet":
        with pytest.raises(
            bot.TransportJournalError,
            match="receipt namespace could not be inspected",
        ):
            bot.block_if_unrelated_receipt_appeared_for_tweet_transport(
                bot.CONFIRMED_REPLY_RECEIPT_FILE
            )
    else:
        with pytest.raises(
            bot.MediaUploadReceiptError,
            match="receipt namespace could not be inspected",
        ):
            bot.block_if_unrelated_receipt_appeared_for_media_transport()


def test_raw_and_bearer_x_create_require_receipt_bound_internal_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No public transport helper can bypass the durable transaction owner."""

    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "unbound X create must stop before transport"
        ),
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="durable transport-journal authorization",
    ):
        bot.x_request(
            "POST",
            "/2/tweets",
            json={"text": "unbound"},
            ambiguous_write=True,
        )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="durable transport-journal authorization",
    ):
        bot.x_request(
            "POST",
            "/2/tweets",
            json={"text": "unbound"},
        )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="no durable transaction authority",
    ):
        bot.x_bearer_request(
            "POST",
            "/2/tweets",
            json={"text": "unbound"},
        )


@pytest.mark.parametrize(
    ("extra_key", "extra_value"),
    [
        ("data", {"text": "different"}),
        ("files", {"media": object()}),
        ("params", {"unexpected": "query"}),
        ("headers", {"Content-Type": "text/plain"}),
        ("allow_redirects", True),
    ],
)
def test_tweet_authority_rejects_every_alternate_request_channel(
    extra_key: str,
    extra_value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"text": "authorised"}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "an alternate request channel must stop before transport"
        ),
    )

    kwargs = {
        "json": payload,
        extra_key: extra_value,
        "ambiguous_write": True,
        "_remote_write_authorization": authority,
    }
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="only one exact JSON body",
    ):
        bot.x_request("POST", "/2/tweets", **kwargs)


def test_tweet_transport_uses_an_isolated_strict_json_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"text": "authorised", "reply": {"in_reply_to_tweet_id": "1"}}
    authority = _armed_x_create_authority(payload)

    def observe(_method: str, _url: str, **kwargs: object) -> bot.requests.Response:
        payload["text"] = "changed outside transport"
        payload["reply"]["in_reply_to_tweet_id"] = "2"
        assert kwargs["json"] == {
            "text": "authorised",
            "reply": {"in_reply_to_tweet_id": "1"},
        }
        assert kwargs["json"] is not payload
        return _x_response(201, {"data": {"id": "123"}})

    monkeypatch.setattr(bot.requests, "request", observe)
    result = bot.x_request(
        "POST",
        "/2/tweets",
        json=payload,
        ambiguous_write=True,
        _remote_write_authorization=authority,
    )
    assert result["data"]["id"] == "123"


@pytest.mark.parametrize(
    "path",
    [
        "/2/media/upload",
        "/1.1/media/upload.json",
        "/1.1/statuses/update.json",
        "/2/tweets?duplicate=true",
    ],
)
def test_bearer_transport_is_read_only(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "bearer write must stop before transport"
        ),
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="Bearer-authenticated X writes",
    ):
        bot.x_bearer_request("POST", path, data={"value": "unit"})


@pytest.mark.parametrize(
    "path",
    [
        "/1.1/media/upload.json",
        "/1.1/statuses/update.json",
        "/2/likes",
    ],
)
def test_generic_x_transport_rejects_unmodelled_write_routes(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "unmodelled write must stop before transport"
        ),
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="no durable transaction policy",
    ):
        bot.x_request("POST", path, data={"value": "unit"})


@pytest.mark.parametrize(
    "path",
    (
        "/2/./tweets",
        "/2/intermediate/../tweets",
        "/%32/tweets",
        "/2/%74weets",
        "/%252532/%252574weets",
        "/2\\tweets",
    ),
)
def test_normalised_raw_and_bearer_x_create_variants_require_authority(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepared/decoded route aliases cannot bypass receipt authority."""

    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "normalised unbound X create must stop before transport"
        ),
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="Prepared and literal X create-route classifications disagree",
    ):
        bot.x_request(
            "POST",
            path,
            json={"text": "unbound"},
            ambiguous_write=True,
        )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="Bearer-authenticated X writes",
    ):
        bot.x_bearer_request(
            "POST",
            path,
            json={"text": "unbound"},
        )


@pytest.mark.parametrize(
    "raw",
    [
        "https://api.x.invalid/prefix",
        "https://api.x.invalid?query=yes",
        "https://api.x.invalid#fragment",
        "https://user@api.x.invalid",
        "https://user:password@api.x.invalid",
    ],
)
def test_x_api_bases_must_be_origin_only(raw: str) -> None:
    with pytest.raises(ValueError):
        bot.normalise_base_url(raw, require_origin=True)


def test_xai_versioned_provider_base_remains_supported() -> None:
    assert (
        bot.normalise_base_url("https://api.x.ai/v1")
        == "https://api.x.ai/v1"
    )


@pytest.mark.parametrize("path", ["/2/tweets", "/2/media/upload"])
def test_prepared_and_literal_create_routes_must_agree(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_base = (
        "X_UPLOAD_BASE" if path == "/2/media/upload" else "X_BASE"
    )
    monkeypatch.setattr(bot, selected_base, "https://api.x.invalid/prefix")
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "route disagreement must stop before transport"
        ),
    )

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="Prepared and literal X create-route classifications disagree",
    ):
        bot.x_request("POST", path)


def test_only_exact_literal_media_post_selects_upload_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "X_BASE", "http://127.0.0.1:18081")
    monkeypatch.setattr(bot, "X_UPLOAD_BASE", "http://127.0.0.1:18082")

    assert (
        bot.x_request_base_url("POST", "/2/media/upload")
        == "http://127.0.0.1:18082"
    )
    for method, path in (
        ("GET", "/2/media/upload"),
        ("POST", "/2/tweets"),
        ("post", "/2/media/upload"),
        ("POST", "/2/media/upload/"),
        ("POST", "/2/media/%75pload"),
    ):
        assert bot.x_request_base_url(method, path) == "http://127.0.0.1:18081"


def test_direct_media_upload_requires_explicit_ambiguous_write_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The low-level media endpoint cannot silently use retryable semantics."""

    calls: list[tuple[str, str]] = []

    def uploaded(method: str, url: str, **_kwargs: object) -> bot.requests.Response:
        calls.append((method, url))
        return _x_response(200, {"data": {"id": "123"}})

    monkeypatch.setattr(bot.requests, "request", uploaded)
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", lambda _op: None)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="exact form and media part",
    ):
        bot.x_request(
            "POST",
            "/2/media/upload",
            data={"media_type": "image/jpeg"},
        )
    assert calls == []


def test_tweet_authority_cannot_bypass_another_write_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "tweet authority must stop before another endpoint"
        ),
    )

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="cannot authorise another X endpoint",
    ):
        bot.x_request(
            "POST",
            "/2/media/upload",
            data={"media_type": "image/jpeg"},
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )


def test_noncanonical_lane_authority_cannot_reach_tweet_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"text": "unit"}
    receipt = {"schema_version": 1, "lifecycle_state": "sending"}
    bot.atomic_write_json(bot.CONFIRMED_REPLY_RECEIPT_FILE, receipt, durable=True)
    prepared = bot.begin_transport_transaction(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        expected_receipt=receipt,
        lane="invented_lane",
        payload=payload,
        source_validator_id="unit-test-source-binding-v2",
        source_validator=lambda lane, observed, body: bool(
            lane == "invented_lane"
            and observed == receipt
            and body == payload
        ),
    )
    authority = bot.arm_transport_transaction(
        Path(prepared.journal_path),
        prepared,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport arming"
        ),
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "noncanonical authority must stop before transport"
        ),
    )

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="lost its exact durable transport authority",
    ):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )


def test_v2_media_upload_uses_ambiguous_write_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preferred upload is one non-repeatable remote write attempt."""

    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def uploaded(
        method: str,
        path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append((method, path, kwargs))
        return {"data": {"id": "media-1"}}

    monkeypatch.setattr(bot, "x_request", uploaded)
    mime_type = "image/png"
    form = {"media_category": "tweet_image", "media_type": mime_type}
    authority = bot.begin_media_upload(
        receipt_path=bot.MEDIA_UPLOAD_RECEIPT_FILE,
        image_path=image,
        lane="quote_image",
        mime_type=mime_type,
        payload_metadata=bot.media_upload_payload_metadata(form),
    )
    payload = bot.bind_media_upload_payload(
        bot.MEDIA_UPLOAD_RECEIPT_FILE,
        authority,
        image_path=image,
        lane="quote_image",
        mime_type=mime_type,
        payload_metadata=bot.media_upload_payload_metadata(form),
    )

    assert bot.upload_media_v2(authority=authority, payload=payload) == "media-1"
    assert len(calls) == 1
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/2/media/upload")
    assert kwargs["ambiguous_write"] is True


def test_pause_before_media_receipt_publication_leaves_no_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(
        bot,
        "require_remote_operation_unpaused",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.RemoteOperationsPaused("paused before begin")
        ),
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail("pause must precede transport"),
    )

    with pytest.raises(bot.RemoteOperationsPaused, match="before begin"):
        bot.upload_media(str(image), lane="quote_image")

    assert not bot.MEDIA_UPLOAD_RECEIPT_FILE.exists()
    assert not bot.media_fence_path_for_receipt(
        bot.MEDIA_UPLOAD_RECEIPT_FILE
    ).exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_final_pretransport_pause_exactly_aborts_media_pair_without_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    pause_observations = iter((False, True))
    request_calls: list[str] = []
    monkeypatch.setattr(
        bot,
        "require_instance_lock_for_remote_write",
        lambda _operation: None,
    )
    monkeypatch.setattr(
        bot,
        "global_remote_writes_paused",
        lambda: next(pause_observations),
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: (
            request_calls.append("later-request"),
            _x_response(403, PRODUCTION_DELETED_REPLY_ERROR),
        )[1],
    )

    with pytest.raises(bot.RemoteOperationsPaused, match="runtime control"):
        bot.upload_media(str(image), lane="quote_image")

    assert request_calls == []
    assert not bot.MEDIA_UPLOAD_RECEIPT_FILE.exists()
    assert not bot.media_fence_path_for_receipt(
        bot.MEDIA_UPLOAD_RECEIPT_FILE
    ).exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False


def test_pause_after_tweet_authority_consumption_is_prospective_and_confirms_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause cannot retroactively cancel a durably committed tweet attempt."""

    receipt = unit_sending_v4_reply_receipt(text="unit reply")
    bot.write_sending_reply_receipt(receipt)
    pause_active = False
    consume_calls = 0
    request_calls = 0
    durable_attempt_observed = False
    real_consume = journal_module.consume_transport_authority

    def consume_then_pause(*args: object, **kwargs: object) -> None:
        nonlocal pause_active, consume_calls
        real_consume(*args, **kwargs)
        consume_calls += 1
        pause_active = True

    def confirmed_once(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal request_calls, durable_attempt_observed
        request_calls += 1
        state = bot.inspect_transport_state(
            bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
        )
        durable_attempt_observed = bool(
            pause_active
            and state.journal is not None
            and state.fence is not None
            and state.journal.document["lifecycle_state"] == "attempting"
        )
        return _x_response(201, {"data": {"id": "123456"}})

    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: pause_active)
    monkeypatch.setattr(
        journal_module,
        "consume_transport_authority",
        consume_then_pause,
    )
    monkeypatch.setattr(bot.requests, "request", confirmed_once)

    result = bot.create_post(
        "unit reply",
        reply_to_id=str(receipt["target_id"]),
        made_with_ai=True,
        prepared_conversational_reply_receipt=receipt,
    )

    assert result == {"data": {"id": "123456"}}
    assert pause_active is True
    assert consume_calls == 1
    assert request_calls == 1
    assert durable_attempt_observed is True
    details = bot.inspect_confirmed_transport_transaction(
        bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    )
    assert details.post_id == "123456"
    confirmed = bot.promote_sending_reply_receipt(
        receipt,
        reply_post_id=details.post_id,
        confirmation_epoch=details.confirmation_epoch,
    )
    from mrs_bot_state_generation import record_receipt_commit

    state = bot.default_state()
    bot.apply_confirmed_reply_receipt(state, confirmed)
    record_receipt_commit(state, confirmed)
    proof = bot.save_state(state, durable=True)
    bot.retire_lane_transport_journal_if_present(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        receipt=confirmed,
        lane="conversational_reply",
        post_id=details.post_id,
        commit_proof=proof,
    )
    bot.remove_confirmed_reply_receipt(confirmed, commit_proof=proof)
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert not Path(details.journal_path).exists()
    assert not bot.fence_path_for_journal(Path(details.journal_path)).exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_pause_after_media_authority_consumption_is_prospective_and_confirms_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed media attempt completes once when pause changes afterwards."""

    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    pause_active = False
    consume_calls = 0
    request_calls = 0
    durable_attempt_observed = False
    real_consume = bot.consume_media_upload_authority

    def consume_then_pause(*args: object, **kwargs: object) -> object:
        nonlocal pause_active, consume_calls
        result = real_consume(*args, **kwargs)
        consume_calls += 1
        pause_active = True
        return result

    def confirmed_once(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal request_calls, durable_attempt_observed
        request_calls += 1
        snapshot = media_receipt_module.inspect_media_upload_receipt(
            bot.MEDIA_UPLOAD_RECEIPT_FILE
        )
        durable_attempt_observed = bool(
            pause_active
            and snapshot is not None
            and snapshot.document["lifecycle_state"] == "sending"
            and bot.media_fence_path_for_receipt(
                bot.MEDIA_UPLOAD_RECEIPT_FILE
            ).exists()
        )
        return _x_response(201, {"data": {"id": "780001"}})

    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: pause_active)
    monkeypatch.setattr(bot, "consume_media_upload_authority", consume_then_pause)
    monkeypatch.setattr(bot.requests, "request", confirmed_once)

    assert bot.upload_media(str(image), lane="quote_image") == "780001"
    assert pause_active is True
    assert consume_calls == 1
    assert request_calls == 1
    assert durable_attempt_observed is True
    confirmed = bot.load_confirmed_media_upload(bot.MEDIA_UPLOAD_RECEIPT_FILE)
    assert confirmed is not None
    assert confirmed.media_id == "780001"
    assert bot._RETAINED_CONFIRMED_POST_SIGINT_GUARD is None
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_media_upload_has_no_health_io_inside_durable_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    events: list[str] = []
    real_begin = bot.begin_media_upload
    real_consume = bot.consume_media_upload_authority
    real_confirm = bot.confirm_media_upload

    def recording_begin(*args: object, **kwargs: object) -> object:
        authority = real_begin(*args, **kwargs)
        events.append("receipt_established")
        return authority

    def recording_consume(*args: object, **kwargs: object) -> object:
        result = real_consume(*args, **kwargs)
        events.append("authority_consumed")
        return result

    def recording_transport(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        events.append("network_transport")
        return _x_response(201, {"data": {"id": "780002"}})

    def recording_confirm(*args: object, **kwargs: object) -> object:
        result = real_confirm(*args, **kwargs)
        events.append("durable_confirmation")
        return result

    monkeypatch.setattr(
        bot,
        "report_bot_health_progress",
        lambda *_args, **_kwargs: events.append("health_write"),
    )
    monkeypatch.setattr(bot, "begin_media_upload", recording_begin)
    monkeypatch.setattr(bot, "consume_media_upload_authority", recording_consume)
    monkeypatch.setattr(bot.requests, "request", recording_transport)
    monkeypatch.setattr(bot, "confirm_media_upload", recording_confirm)

    assert bot.upload_media(str(image), lane="quote_image") == "780002"
    assert events == [
        "receipt_established",
        "authority_consumed",
        "network_transport",
        "durable_confirmation",
    ]


def test_public_post_has_no_health_io_inside_durable_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = unit_sending_v4_reply_receipt(text="transaction ordering")
    bot.write_sending_reply_receipt(receipt)
    events: list[str] = []
    real_begin = bot.begin_transport_transaction
    real_arm = bot.arm_transport_transaction
    real_consume = journal_module.consume_transport_authority
    real_confirm = bot.confirm_transport_transaction

    def recording_begin(*args: object, **kwargs: object) -> object:
        authority = real_begin(*args, **kwargs)
        events.append("journal_established")
        return authority

    def recording_arm(*args: object, **kwargs: object) -> object:
        authority = real_arm(*args, **kwargs)
        events.append("authority_issued")
        return authority

    def recording_consume(*args: object, **kwargs: object) -> object:
        result = real_consume(*args, **kwargs)
        events.append("authority_consumed")
        return result

    def recording_transport(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        events.append("network_transport")
        return _x_response(201, {"data": {"id": "880002"}})

    def recording_confirm(*args: object, **kwargs: object) -> object:
        result = real_confirm(*args, **kwargs)
        events.append("durable_confirmation")
        return result

    monkeypatch.setattr(
        bot,
        "report_bot_health_progress",
        lambda *_args, **_kwargs: events.append("health_write"),
    )
    monkeypatch.setattr(bot, "begin_transport_transaction", recording_begin)
    monkeypatch.setattr(bot, "arm_transport_transaction", recording_arm)
    monkeypatch.setattr(
        journal_module,
        "consume_transport_authority",
        recording_consume,
    )
    monkeypatch.setattr(bot.requests, "request", recording_transport)
    monkeypatch.setattr(bot, "confirm_transport_transaction", recording_confirm)

    result = bot.create_post(
        str(receipt["reply_text"]),
        reply_to_id=str(receipt["target_id"]),
        made_with_ai=False,
        prepared_conversational_reply_receipt=receipt,
    )

    assert result == {"data": {"id": "880002"}}
    assert events == [
        "journal_established",
        "authority_issued",
        "authority_consumed",
        "network_transport",
        "durable_confirmation",
    ]


def test_interrupted_outer_health_update_cannot_leave_a_write_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["next_quote_post_epoch"] = 0
    monkeypatch.setattr(bot, "lane_paused", lambda _lane: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda _state, **_kwargs: False)

    def interrupted_health_update(stage: str, **_kwargs: object) -> None:
        assert stage == "quote_post"
        raise KeyboardInterrupt("injected before quote transaction")

    monkeypatch.setattr(bot, "report_bot_health_progress", interrupted_health_update)
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *_args, **_kwargs: pytest.fail(
            "posting must not begin after the outer progress update is interrupted"
        ),
    )

    with pytest.raises(KeyboardInterrupt, match="before quote transaction"):
        bot._run_due_quote_post_for_tick(set(), set(), state, current=1)

    for receipt_path in (
        bot.MEDIA_UPLOAD_RECEIPT_FILE,
        bot.REGULAR_POST_RECEIPT_FILE,
        bot.MEME_POST_RECEIPT_FILE,
        bot.CONFIRMED_REPLY_RECEIPT_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ):
        journal_path = bot.journal_path_for_receipt(receipt_path)
        assert not receipt_path.exists()
        assert not journal_path.exists()
        assert not bot.fence_path_for_journal(journal_path).exists()
    assert not bot.media_fence_path_for_receipt(
        bot.MEDIA_UPLOAD_RECEIPT_FILE
    ).exists()


def test_pause_after_media_authority_consumption_cannot_abort_and_records_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    prior_sigint_handler = signal.getsignal(signal.SIGINT)
    marker_guard_observations: list[bool] = []
    real_record_ambiguous = bot.record_ambiguous_remote_post

    monkeypatch.setattr(
        bot,
        "require_remote_operation_unpaused",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "require_instance_lock_for_remote_write",
        lambda _operation: None,
    )

    def consumed_then_paused(
        *,
        authority: bot.MediaUploadAuthority,
        payload: bot.ReceiptBoundMediaPayload,
    ) -> str:
        form = {
            "media_category": "tweet_image",
            "media_type": payload.mime_type,
        }
        bot.consume_media_upload_authority(
            bot.MEDIA_UPLOAD_RECEIPT_FILE,
            authority,
            payload=payload,
            lane=authority.lane,
            mime_type=payload.mime_type,
            payload_metadata=bot.media_upload_payload_metadata(form),
        )
        raise bot.RemoteOperationsPaused("synthetic post-consumption pause")

    def record_while_guarded(payload: dict[str, object]) -> None:
        marker_guard_observations.append(
            signal.getsignal(signal.SIGINT) != prior_sigint_handler
        )
        real_record_ambiguous(payload)

    monkeypatch.setattr(bot, "upload_media_v2", consumed_then_paused)
    monkeypatch.setattr(bot, "record_ambiguous_remote_post", record_while_guarded)

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="locally paused media upload",
    ):
        bot.upload_media(str(image), lane="quote_image")

    assert marker_guard_observations == [True]
    assert signal.getsignal(signal.SIGINT) == prior_sigint_handler
    assert bot.MEDIA_UPLOAD_RECEIPT_FILE.exists()
    assert bot.media_fence_path_for_receipt(
        bot.MEDIA_UPLOAD_RECEIPT_FILE
    ).exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    "remote_error",
    [
        bot.AmbiguousRemotePostOutcome("timeout", service="x"),
        bot.ApiError("server error", service="x", status_code=500),
        RuntimeError("unclassified post-attempt failure"),
    ],
)
def test_v2_media_upload_failure_never_calls_legacy_fallback(
    remote_error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No v1.1 upload follows a v2 outcome which may be remote."""

    legacy_calls = 0

    image = tmp_path / "image.png"
    image.write_bytes(b"image")

    def failed_v2(**_kwargs: object) -> str:
        raise remote_error

    def legacy(_image_path: str) -> str:
        nonlocal legacy_calls
        legacy_calls += 1
        return "legacy-media"

    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda _op: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "upload_media_v2", failed_v2)
    monkeypatch.setattr(bot, "upload_media_v1_1", legacy)

    with pytest.raises(type(remote_error), match=str(remote_error)):
        bot.upload_media(str(image), lane="quote_image")

    assert legacy_calls == 0


def test_v2_media_transport_timeout_never_calls_legacy_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout after entering Requests is an ambiguous terminal outcome."""

    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    legacy_calls = 0

    def timed_out(*_args: object, **_kwargs: object) -> bot.requests.Response:
        raise bot.requests.ReadTimeout("response lost")

    def legacy(_image_path: str) -> str:
        nonlocal legacy_calls
        legacy_calls += 1
        return "legacy-media"

    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_a, **_k: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot.requests, "request", timed_out)
    monkeypatch.setattr(bot, "upload_media_v1_1", legacy)

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="response lost"):
        bot.upload_media(str(image), lane="quote_image")

    assert legacy_calls == 0
    assert bot.durable_remote_write_safety_barrier_exists() is True
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"data": {}},
        {"data": {"id": ""}},
        {"data": {"id": []}},
    ],
)
def test_v2_media_success_without_usable_id_never_calls_legacy_fallback(
    result: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2xx-shaped result without an upload identity remains ambiguous."""

    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    legacy_calls = 0

    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_a, **_k: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "x_request", lambda *_a, **_k: result)

    def legacy(_image_path: str) -> str:
        nonlocal legacy_calls
        legacy_calls += 1
        return "legacy-media"

    monkeypatch.setattr(bot, "upload_media_v1_1", legacy)

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="valid data.id"):
        bot.upload_media(str(image), lane="quote_image")

    assert legacy_calls == 0


def test_create_post_requires_exactly_one_prepared_durable_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "unbound create_post must stop before X transport"
        ),
    )

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="exactly one prepared durable transaction receipt",
    ):
        bot.create_post("unbound")


def _install_attempting_context_outbox(*, remote_phase: bool | None):
    outbox = outbox_module.HistoricalContextOutbox(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE
    )
    parent_id = "1800000001"
    outbox.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id="a" * 64,
        quote_text="A reviewed historical-context quotation.",
    )
    outbox.claim_attempt(parent_id, started_epoch=1_800_000_001)
    outbox.bind_attempt_source_receipt(
        parent_id,
        attempt_number=1,
        source_receipt_sha256="d" * 64,
        source_receipt_attempt_number=1,
    )
    if remote_phase is True:
        outbox.mark_remote_transaction_started(parent_id, attempt_number=1)
    elif remote_phase is None:
        snapshot = outbox.snapshot()
        del snapshot["obligations"][parent_id]["context_reply"][
            "remote_transaction_started"
        ]
        outbox_module._atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
            snapshot,
        )
    return outbox


def _claimed_context_outbox_callbacks(
    *,
    parent_post_id: str,
    quote_id: str,
    quote_text: str,
    started_epoch: int,
) -> tuple[
    outbox_module.HistoricalContextOutbox,
    dict[str, object],
]:
    """Build the exact production outbox lifecycle for a direct store test."""

    outbox = outbox_module.HistoricalContextOutbox(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE
    )
    outbox.enqueue(
        parent_post_id,
        main_post_confirmed_epoch=started_epoch,
        quote_id=quote_id,
        quote_text=quote_text,
    )
    claimed = outbox.claim_attempt(
        parent_post_id,
        started_epoch=started_epoch,
    )
    attempt_number = int(claimed["context_reply"]["attempt_count"])
    callbacks: dict[str, object] = {
        "on_source_receipt_published": (
            lambda source_sha256, source_attempt_number: (
                outbox.bind_attempt_source_receipt(
                    parent_post_id,
                    attempt_number=attempt_number,
                    source_receipt_sha256=source_sha256,
                    source_receipt_attempt_number=source_attempt_number,
                )
            )
        ),
        "on_remote_transaction_started": (
            lambda: outbox.mark_remote_transaction_started(
                parent_post_id,
                attempt_number=attempt_number,
            )
        ),
        "on_confirmed_receipt": (
            lambda confirmed_receipt, confirmation_epoch: (
                outbox.record_confirmed(
                    parent_post_id,
                    attempt_number=attempt_number,
                    reply_post_id=confirmed_receipt["reply_post_id"],
                    confirmed_epoch=confirmation_epoch,
                )
            )
        ),
    }
    return outbox, callbacks


@pytest.mark.parametrize("remote_phase", [True, None])
def test_remote_or_legacy_attempting_outbox_is_a_global_barrier_when_runtime_unavailable(
    remote_phase: bool | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lost source files cannot hide a durable possibly transmitted attempt."""

    outbox = _install_attempting_context_outbox(remote_phase=remote_phase)
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        "injected unavailable runtime",
    )

    assert bot.historical_context_outbox_remote_attempt_is_blocking() is True
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="outbox attempt may have reached remote transport",
    ):
        bot.block_if_ambiguous_remote_post()
    assert bot._process_due_historical_context_obligations(store=outbox) == []
    assert outbox.get("1800000001")["context_reply"]["state"] == (
        "context_reply_attempting"
    )


def test_explicit_pre_remote_attempting_outbox_is_not_a_global_barrier() -> None:
    """The durable false phase remains eligible for bounded local recovery."""

    _install_attempting_context_outbox(remote_phase=False)

    assert bot.historical_context_outbox_remote_attempt_is_blocking() is False
    assert bot.ambiguous_remote_post_is_blocking() is False
    bot.block_if_ambiguous_remote_post()


@pytest.mark.parametrize(
    "outbox_scenario",
    ["exact", "unrelated_attempt", "missing_remote_phase", "missing_obligation"],
)
def test_exact_armed_context_attempt_requires_own_row_and_blocks_unrelated_barrier(
    outbox_scenario: str,
) -> None:
    """Final preflight exempts only its exact armed remote-started row."""

    receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "123",
        "quote_id": "a" * 64,
        "reply_text": "reviewed context",
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": 1,
    }
    bot.atomic_write_json(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE, receipt)
    payload = {
        "text": receipt["reply_text"],
        "reply": {"in_reply_to_tweet_id": receipt["parent_post_id"]},
    }
    prepared = bot.begin_transport_transaction(
        receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        expected_receipt=receipt,
        lane="historical_context_reply",
        payload=payload,
        source_validator_id="unit-test-context-outbox-binding-v1",
        source_validator=lambda lane, observed, body: bool(
            lane == "historical_context_reply"
            and observed == receipt
            and body == payload
        ),
    )
    authority = bot.arm_transport_transaction(
        Path(prepared.journal_path),
        prepared,
        mutation_authority=bot.transaction_mutation_authority(
            "focused exact context outbox arming"
        ),
    )
    outbox = outbox_module.HistoricalContextOutbox(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE
    )
    if outbox_scenario != "missing_obligation":
        outbox.enqueue(
            "123",
            main_post_confirmed_epoch=1_800_000_000,
            quote_id=receipt["quote_id"],
            quote_text="A reviewed quotation.",
        )
        outbox.claim_attempt("123", started_epoch=1_800_000_001)
        outbox.bind_attempt_source_receipt(
            "123",
            attempt_number=1,
            source_receipt_sha256=hashlib.sha256(
                bot.canonical_atomic_json_bytes(receipt)
            ).hexdigest(),
            source_receipt_attempt_number=1,
        )
        if outbox_scenario != "missing_remote_phase":
            outbox.mark_remote_transaction_started("123", attempt_number=1)
    if outbox_scenario == "unrelated_attempt":
        outbox.enqueue(
            "124",
            main_post_confirmed_epoch=1_800_000_000,
            quote_id="b" * 64,
            quote_text="Another reviewed quotation.",
        )
        outbox.claim_attempt("124", started_epoch=1_800_000_001)
        outbox.bind_attempt_source_receipt(
            "124",
            attempt_number=1,
            source_receipt_sha256="e" * 64,
            source_receipt_attempt_number=1,
        )
        outbox.mark_remote_transaction_started("124", attempt_number=1)

    if outbox_scenario != "exact":
        with pytest.raises(bot.AmbiguousRemotePostOutcome, match="outbox attempt"):
            bot.require_remote_operation_unpaused(
                "focused historical-context transport",
                transaction_authorization=authority,
            )
    else:
        bot.require_remote_operation_unpaused(
            "focused historical-context transport",
            transaction_authorization=authority,
        )


def test_blank_x_create_response_preserves_exact_evidence_and_fails_closed(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production incident body is recoverable from one ordinary log event."""

    caplog.set_level(logging.WARNING, logger=bot.log.name)
    caplog.clear()
    body = PRODUCTION_BLANK_CREATE_RESPONSE_BODY
    response = _raw_x_response(201, body)
    response.encoding = "utf-8"
    response.elapsed = timedelta(milliseconds=123)
    response.headers.update(
        {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "Request-ID": "intermediary-request-1",
            "Traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "X-Request-ID": "x-request-1",
            "X-Transaction-ID": "x-transaction-1",
            "X-Correlation-ID": "rejected\ncontrol",
            "Set-Cookie": "session=must-not-appear",
            "Authorization": "Bearer must-not-appear",
            "Cookie": "must-not-appear",
            "X-Arbitrary-Diagnostic": "must-not-appear",
            "Server": "must-not-appear",
        }
    )
    original_json = response.json
    json_calls = 0
    decoded_objects: list[object] = []

    def decode_once() -> object:
        nonlocal json_calls
        json_calls += 1
        decoded = original_json()
        decoded_objects.append(decoded)
        return decoded

    response.json = decode_once
    remote_calls = 0

    def anomalous_create(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return response

    observed_record_api_error_statuses: list[int | None] = []
    real_record_api_error = bot.record_api_error

    def capture_record_api_error(
        state: dict,
        error: Exception,
        service: str,
        *,
        scope: str = "api",
    ) -> None:
        observed_record_api_error_statuses.append(
            getattr(error, "status_code", None)
        )
        real_record_api_error(state, error, service, scope=scope)

    monkeypatch.setattr(bot.requests, "request", anomalous_create)
    monkeypatch.setattr(bot, "record_api_error", capture_record_api_error)
    sending = unit_sending_v4_reply_receipt(
        target_id="100",
        text="Anomaly fixture reply.",
    )
    state = bot.default_state()

    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        bot.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert remote_calls == 1
    assert json_calls == 1
    expected_decoded = {
        "data": {"edit_history_tweet_ids": [], "id": "", "text": ""}
    }
    assert decoded_objects == [expected_decoded]
    anomaly_records = _x_create_anomaly_records(caplog)
    assert len(anomaly_records) == 1
    message, event = anomaly_records[0]
    event_json = message.split(" ", 1)[1]
    assert "\n" not in message
    assert "\r" not in message
    assert event_json == json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert event["schema_version"] == 1
    assert event["event"] == bot.X_CREATE_RESPONSE_ANOMALY_EVENT
    assert type(event["recorded_at"]) is int
    assert event["http_status"] == 201
    assert event["reason"] == "data_id_blank"
    assert event["response_elapsed_ms"] == 123
    assert event["response_encoding"] == "utf-8"
    assert event["content_type"] == "application/json; charset=utf-8"
    assert event["content_length_header"] == str(len(body))
    assert event["safe_correlation_headers"] == {
        "request-id": "intermediary-request-1",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "x-request-id": "x-request-1",
        "x-transaction-id": "x-transaction-1",
    }
    assert event["raw_body_length"] == len(body)
    assert event["raw_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert event["raw_body_complete"] is True
    assert event["raw_body_encoding"] == "base64"
    assert base64.b64decode(event["raw_body_base64"], validate=True) == body
    assert event["json_decode_succeeded"] is True
    assert event["json_error_type"] is None
    assert event["json_error_position"] is None
    assert event["decoded_top_level_type"] == "object"
    assert event["decoded_top_level_keys"] == ["data"]
    assert event["decoded_top_level_keys_complete"] is True
    assert event["data_type"] == "object"
    assert event["data_id_type"] == "string"
    assert event["data_id_value"] == ""
    assert event["data_id_value_complete"] is True
    assert event["data_id_character_length"] == 0
    assert event["data_text_type"] == "string"
    assert event["data_text_character_length"] == 0
    assert event["edit_history_tweet_ids_type"] == "array"
    assert event["edit_history_tweet_ids_count"] == 0
    assert event["target_id"] == "100"
    assert event["transport_lane"] == "conversational_reply"
    assert event["diagnostic_sha256"] == _recompute_x_create_diagnostic_sha256(
        event
    )

    journal_path = bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    transport_state = bot.inspect_transport_state(journal_path)
    assert transport_state.journal is not None
    assert transport_state.fence is not None
    assert transport_state.journal.document["lifecycle_state"] == "attempting"
    assert transport_state.fence.document["lifecycle_state"] == "prepared"
    assert event["transaction_id"] == transport_state.journal.document["transaction_id"]
    assert (
        event["canonical_payload_sha256"]
        == transport_state.journal.document["remote_payload_sha256"]
    )
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert observed_record_api_error_statuses == [201]
    assert caught.value.status_code == 201
    assert caught.value.response_body_length == len(body)
    assert caught.value.response_body_sha256 == hashlib.sha256(body).hexdigest()
    assert caught.value.diagnostic_sha256 == event["diagnostic_sha256"]
    assert caught.value.diagnostic_event == bot.X_CREATE_RESPONSE_ANOMALY_EVENT
    api_error_messages = [
        record.getMessage()
        for record in caplog.records
        if "Recorded write/x API error." in record.getMessage()
    ]
    assert len(api_error_messages) == 1
    assert "status_code=201" in api_error_messages[0]
    assert f"diagnostic_sha256={event['diagnostic_sha256']}" in api_error_messages[0]
    assert (
        f"diagnostic_event={bot.X_CREATE_RESPONSE_ANOMALY_EVENT}"
        in api_error_messages[0]
    )
    encoded_body = base64.b64encode(body).decode("ascii")
    assert caplog.text.count(encoded_body) == 1
    for forbidden in (
        "Authorization",
        "session=must-not-appear",
        "Bearer must-not-appear",
        "Cookie",
        "X-Arbitrary-Diagnostic",
        "Server",
        "rejected\\ncontrol",
        "Anomaly fixture reply.",
    ):
        assert forbidden not in message
        assert forbidden not in caplog.text


def test_valid_x_create_response_confirms_without_anomaly_or_mutation(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid public result remains the exact once-decoded response object."""

    caplog.set_level(logging.ERROR, logger=bot.log.name)
    caplog.clear()
    sending = unit_sending_v4_reply_receipt(
        target_id="100",
        text="Valid fixture reply.",
    )
    bot.write_sending_reply_receipt(sending)
    decoded = {
        "data": {
            "edit_history_tweet_ids": ["123456789"],
            "id": "123456789",
            "text": "@example Valid fixture reply.",
        }
    }
    response = _raw_x_response(
        201,
        json.dumps(decoded, separators=(",", ":")).encode("utf-8"),
    )
    json_calls = 0

    def return_same_decoded_object() -> object:
        nonlocal json_calls
        json_calls += 1
        return decoded

    response.json = return_same_decoded_object
    remote_calls = 0

    def valid_create(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return response

    monkeypatch.setattr(bot.requests, "request", valid_create)

    result = bot.create_post(
        str(sending["reply_text"]),
        reply_to_id=str(sending["target_id"]),
        made_with_ai=False,
        prepared_conversational_reply_receipt=sending,
    )

    assert result is decoded
    assert result == decoded
    assert json_calls == 1
    assert remote_calls == 1
    assert _x_create_anomaly_records(caplog) == []
    details = bot.inspect_confirmed_transport_transaction(
        bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    )
    assert details.post_id == "123456789"
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


@pytest.mark.parametrize(
    ("body", "reason", "json_decode_succeeded", "expected_json_calls"),
    [
        (b"", "empty_response_body", False, 0),
        (b"{}", "data_absent", True, 1),
        (b'{"data":[]}', "data_not_object", True, 1),
        (b'{"data":{}}', "data_id_absent", True, 1),
        (b'{"data":{"id":"not-numeric"}}', "data_id_non_numeric", True, 1),
    ],
)
def test_x_create_response_schema_anomalies_emit_bounded_evidence(
    body: bytes,
    reason: str,
    json_decode_succeeded: bool,
    expected_json_calls: int,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.ERROR, logger=bot.log.name)
    caplog.clear()
    response = _raw_x_response(200, body)
    original_json = response.json
    json_calls = 0

    def count_json_decode() -> object:
        nonlocal json_calls
        json_calls += 1
        return original_json()

    response.json = count_json_decode
    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert json_calls == expected_json_calls
    assert caught.value.status_code == 200
    anomaly_records = _x_create_anomaly_records(caplog)
    assert len(anomaly_records) == 1
    _, event = anomaly_records[0]
    assert event["reason"] == reason
    assert event["json_decode_succeeded"] is json_decode_succeeded
    assert base64.b64decode(event["raw_body_base64"], validate=True) == body
    assert event["raw_body_length"] == len(body)
    assert event["raw_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert event["diagnostic_sha256"] == _recompute_x_create_diagnostic_sha256(
        event
    )
    transport_state = bot.inspect_transport_state(Path(authority.journal_path))
    assert transport_state.journal is not None
    assert transport_state.fence is not None
    assert transport_state.journal.document["lifecycle_state"] == "attempting"
    assert transport_state.fence.document["lifecycle_state"] == "prepared"


def test_invalid_json_x_create_logs_exact_body_and_decoder_failure(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.ERROR, logger=bot.log.name)
    caplog.clear()
    body = b'{"data":'
    response = _raw_x_response(202, body)
    original_json = response.json
    json_calls = 0

    def invalid_json_once() -> object:
        nonlocal json_calls
        json_calls += 1
        return original_json()

    response.json = invalid_json_once
    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert json_calls == 1
    assert caught.value.status_code == 202
    anomaly_records = _x_create_anomaly_records(caplog)
    assert len(anomaly_records) == 1
    _, event = anomaly_records[0]
    assert event["reason"] == "json_decode_error"
    assert event["json_decode_succeeded"] is False
    assert event["json_error_type"] == "JSONDecodeError"
    assert event["json_error_position"] == 8
    assert event["decoded_top_level_type"] is None
    assert base64.b64decode(event["raw_body_base64"], validate=True) == body
    assert event["raw_body_length"] == len(body)
    assert event["raw_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert event["diagnostic_sha256"] == _recompute_x_create_diagnostic_sha256(
        event
    )
    transport_state = bot.inspect_transport_state(Path(authority.journal_path))
    assert transport_state.journal is not None
    assert transport_state.journal.document["lifecycle_state"] == "attempting"


@pytest.mark.parametrize(
    ("body", "decoded_type"),
    [
        (b"[]", "array"),
        (b'"scalar"', "string"),
    ],
)
def test_non_object_json_x_create_logs_exact_decoded_type(
    body: bytes,
    decoded_type: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.ERROR, logger=bot.log.name)
    caplog.clear()
    response = _raw_x_response(200, body)
    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    anomaly_records = _x_create_anomaly_records(caplog)
    assert len(anomaly_records) == 1
    _, event = anomaly_records[0]
    assert event["reason"] == "decoded_top_level_not_object"
    assert event["json_decode_succeeded"] is True
    assert event["decoded_top_level_type"] == decoded_type
    assert event["decoded_top_level_keys"] is None
    assert base64.b64decode(event["raw_body_base64"], validate=True) == body
    assert event["diagnostic_sha256"] == _recompute_x_create_diagnostic_sha256(
        event
    )
    transport_state = bot.inspect_transport_state(Path(authority.journal_path))
    assert transport_state.journal is not None
    assert transport_state.journal.document["lifecycle_state"] == "attempting"


def test_oversized_x_create_response_logs_only_full_length_and_hash(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.ERROR, logger=bot.log.name)
    caplog.clear()
    oversized_padding = "x" * (bot.X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES + 1)
    body = json.dumps(
        {"data": {"id": ""}, "padding": oversized_padding},
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(body) > bot.X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES
    response = _raw_x_response(200, body)
    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    anomaly_records = _x_create_anomaly_records(caplog)
    assert len(anomaly_records) == 1
    _, event = anomaly_records[0]
    assert event["reason"] == "data_id_blank"
    assert event["raw_body_length"] == len(body)
    assert event["raw_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert event["raw_body_complete"] is False
    assert event["raw_body_encoding"] is None
    assert event["raw_body_base64"] is None
    assert event["diagnostic_sha256"] == _recompute_x_create_diagnostic_sha256(
        event
    )
    assert "x" * 100 not in caplog.text
    transport_state = bot.inspect_transport_state(Path(authority.journal_path))
    assert transport_state.journal is not None
    assert transport_state.journal.document["lifecycle_state"] == "attempting"


def test_x_create_response_anomaly_event_is_exactly_scoped(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.ERROR, logger=bot.log.name)
    caplog.clear()
    remote_calls = 0

    def read_response(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(200, {"data": {"id": ""}})

    monkeypatch.setattr(bot.requests, "request", read_response)
    assert bot.x_request("GET", "/2/tweets/100") == {"data": {"id": ""}}
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="no durable transaction policy",
    ):
        bot.x_request("POST", "/2/not-tweets")

    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)

    def non_success_create(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(500, {"detail": "server error"})

    monkeypatch.setattr(bot.requests, "request", non_success_create)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert remote_calls == 2
    assert _x_create_anomaly_records(caplog) == []


def test_media_create_response_never_emits_tweet_create_anomaly_event(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even an analogous media response stays outside the tweet-create event."""

    caplog.set_level(logging.ERROR, logger=bot.log.name)
    caplog.clear()
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    remote_calls = 0

    def anomalous_media_create(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _raw_x_response(201, PRODUCTION_BLANK_CREATE_RESPONSE_BODY)

    monkeypatch.setattr(bot.requests, "request", anomalous_media_create)

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="valid data.id"):
        bot.upload_media(str(image), lane="quote_image")

    assert remote_calls == 1
    assert _x_create_anomaly_records(caplog) == []


def _configure_approved_mention_candidate(
    state: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider_calls: list[str],
    fixed_epoch: int = 2_000_000_000,
) -> dict[str, object]:
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher a substantive direct mention",
        "entities": {
            "mentions": [{"id": "12345", "username": "MrsMThatcher"}]
        },
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    context = unit_reply_context(
        target_id="100",
        contribution=str(mention["text"]),
    )

    def approved(
        actual_context: dict[str, object],
        *_args: object,
        **_kwargs: object,
    ) -> str:
        provider_calls.append("called")
        assert actual_context == context
        return unit_approved_reply(
            actual_context,
            text="Conviction still matters.",
            mode="opinion_or_principle",
        )

    enabled = dict(bot.single_call_reply)
    enabled["enabled"] = True
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "single_call_reply", enabled)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(fixed_epoch),
    )
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(
        bot,
        "is_probably_spam_or_not_worth_replying",
        lambda _text: False,
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda *_args: PreparedReplyContext(dict(context), {}),
    )
    monkeypatch.setattr(
        bot,
        "reply_media_context_for_candidate",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(approved),
    )
    state["last_reply_epoch"] = 0
    return mention


def _configure_approved_quote_candidate(
    state: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider_calls: list[str],
    fixed_epoch: int = 2_000_000_000,
) -> dict[str, object]:
    own_post = {
        "id": "900",
        "author_id": "12345",
        "text": "An original post.",
        "conversation_id": "900",
        "referenced_tweets": [],
    }
    quote_post = {
        "id": "910",
        "author_id": "777",
        "text": "A substantive comment.",
        "conversation_id": "910",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }

    def approved(
        actual_context: dict[str, object],
        *_args: object,
        **_kwargs: object,
    ) -> str:
        provider_calls.append("called")
        return unit_approved_reply(
            actual_context,
            text="Conviction still matters.",
            mode="opinion_or_principle",
        )

    enabled = dict(bot.single_call_reply)
    enabled["enabled"] = True
    state["recent_own_post_ids"] = ["900"]
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime(
        "%Y-%m-%d"
    )
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "single_call_reply", enabled)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(fixed_epoch),
    )
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda _state: ["900"])
    patch_tweet_lookup_method(
        monkeypatch, "get_cached",
        lambda *_args, **_kwargs: dict(own_post),
    )
    monkeypatch.setattr(
        bot,
        "get_quote_tweets_for_posts",
        lambda *_args, **_kwargs: {"900": [dict(quote_post)]},
    )
    monkeypatch.setattr(bot, "quote_tweet_is_old_enough", lambda _tweet: True)
    monkeypatch.setattr(
        bot,
        "is_probably_spam_or_not_worth_replying",
        lambda _text: False,
    )
    monkeypatch.setattr(
        bot,
        "reply_media_context_for_candidate",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(approved),
    )
    return quote_post


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 408, 409, 425, 429, 500])
def test_x_create_non_success_is_ambiguous_by_default(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def rejected(
        _method: str,
        _url: str,
        **kwargs: object,
    ) -> bot.requests.Response:
        calls.append(kwargs)
        return _x_response(
            status_code,
            {"errors": [{"detail": "generic response without a no-create proof"}]},
        )

    monkeypatch.setattr(bot.requests, "request", rejected)

    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert len(calls) == 1


@pytest.mark.parametrize("status_code", [403, 404, 429])
def test_generic_reply_create_403_404_and_429_remain_ambiguous(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            status_code,
            {
                "detail": "generic response without target-specific semantics",
                "status": status_code,
                "title": "Request failed",
                "type": "about:blank",
            },
        ),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


@pytest.mark.parametrize(
    "body",
    [
        {
            "detail": "Tweet is unavailable",
            "status": 403,
            "title": "Client Forbidden",
            "type": "about:blank",
        },
        {
            "errors": [
                {
                    "detail": "Tweet is unavailable",
                    "title": "Client Forbidden",
                }
            ],
            "status": 403,
            "title": "Forbidden",
            "type": "about:blank",
        },
    ],
    ids=("root-title", "nested-error-title"),
)
def test_global_denial_title_overrides_target_specific_detail(
    body: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            body,
        ),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_legacy_raw_error_fallback_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "legacy_note": "Tweet is unavailable",
        "status": 403,
        "title": "Forbidden",
        "type": "about:blank",
    }
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(403, body),
    )

    with pytest.raises(bot.ApiError) as caught:
        bot.x_request("GET", "/2/tweets/100")
    assert (
        bot.classify_x_api_error(caught.value)
        == bot.X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE
    )

    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_consumed_reply_rejection_requires_classifier_issued_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    source, authority = _armed_x_create_transaction(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )

    with pytest.raises(bot.ProvedRemotePostNonSuccess) as caught:
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    journal_path = Path(authority.journal_path)
    fence_path = bot.fence_path_for_journal(journal_path)
    genuine_proof = caught.value.remote_non_success_proof
    transaction_key = (str(journal_path.absolute()), authority.transaction_id)
    assert journal_module._consumed_authority_objects[transaction_key] is authority
    assert journal_module._transaction_source_bindings[transaction_key] is source
    proof_registration = error_semantics._issued_rejection_proofs[
        id(genuine_proof)
    ]
    assert proof_registration[9] is authority
    assert proof_registration[10] is source
    forged_proof = error_semantics.DeterministicReplyCreateRejectionProof(
        error_semantics._REJECTION_PROOF_SECRET,
        classification=(
            genuine_proof._DeterministicReplyCreateRejectionProof__classification
        ),
        status_code=(
            genuine_proof._DeterministicReplyCreateRejectionProof__status_code
        ),
        response_sha256=(
            genuine_proof._DeterministicReplyCreateRejectionProof__response_sha256
        ),
        transaction_identity=(
            genuine_proof._DeterministicReplyCreateRejectionProof__transaction_identity
        ),
        payload_sha256=(
            genuine_proof._DeterministicReplyCreateRejectionProof__payload_sha256
        ),
        target_id=(
            genuine_proof._DeterministicReplyCreateRejectionProof__target_id
        ),
        payload_bytes=(
            genuine_proof._DeterministicReplyCreateRejectionProof__payload_bytes
        ),
    )
    forged_error = bot.ProvedRemotePostNonSuccess(
        "caller-rebound proof",
        service="x",
        status_code=403,
        request_method="POST",
        request_path="/2/tweets",
        remote_non_success_proof=forged_proof,
    )
    assert bot.api_error_proves_remote_non_success(forged_error) is False
    for fabricated in (
        True,
        bot.ApiError(
            "caller says it failed",
            service="x",
            status_code=403,
            request_method="POST",
            request_path="/2/tweets",
        ),
        forged_proof,
    ):
        with pytest.raises(
            bot.TransportJournalError,
            match="transaction identity is stale",
        ):
            bot.retire_consumed_transport_transaction_after_proved_remote_non_success(
                source_binding=source,
                authority=authority,
                remote_non_success_proof=fabricated,
                mutation_authority=bot.transaction_mutation_authority(
                    "focused fabricated rejection retirement"
                ),
            )
        assert journal_path.exists()
        assert fence_path.exists()

    reconstructed_source = replace(source)
    copied_authority = replace(
        authority,
        source_binding_identity=id(reconstructed_source),
    )
    with pytest.raises(
        bot.TransportJournalError,
        match="authority was not consumed by this process",
    ):
        bot.retire_consumed_transport_transaction_after_proved_remote_non_success(
            source_binding=reconstructed_source,
            authority=copied_authority,
            remote_non_success_proof=caught.value.remote_non_success_proof,
            mutation_authority=bot.transaction_mutation_authority(
                "focused copied source rejection retirement"
            ),
        )
    assert journal_path.exists()
    assert fence_path.exists()

    bot.retire_consumed_transport_transaction_after_proved_remote_non_success(
        source_binding=source,
        authority=authority,
        remote_non_success_proof=caught.value.remote_non_success_proof,
        mutation_authority=bot.transaction_mutation_authority(
            "focused proved rejection retirement"
        ),
    )

    assert not journal_path.exists()
    assert not fence_path.exists()
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_caller_synthesised_response_cannot_mint_registered_rejection_proof() -> None:
    assert not hasattr(
        journal_module,
        "_consume_transport_authority_for_x_request",
    )
    assert not hasattr(
        error_semantics,
        "_bind_consumed_transport_attempt_to_x_response",
    )
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    _source, authority = _armed_x_create_transaction(payload)
    assert (
        bot.consume_transport_authority(
            Path(authority.journal_path),
            authority,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
            expected_receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        )
        is None
    )
    raw_body = json.dumps(PRODUCTION_DELETED_REPLY_ERROR).encode("utf-8")
    validated = error_semantics.parse_validated_x_error_response(
        raw_body,
        status_code=403,
    )
    api_error = bot.ApiError(
        "synthetic production-style error",
        service="x",
        status_code=403,
        request_method="POST",
        request_path="/2/tweets",
        x_error_response=validated,
        x_error_message_fallback=False,
    )
    actual_response = _x_response(403, PRODUCTION_DELETED_REPLY_ERROR)
    fabricated_response = journal_module._ConsumedXResponse(
        transaction_key=(
            str(Path(authority.journal_path).absolute()),
            authority.transaction_id,
        ),
        response_identity=id(actual_response),
        status_code=403,
        body_sha256=hashlib.sha256(raw_body).hexdigest(),
        authority_identity=id(authority),
    )

    assert (
        error_semantics._issue_reply_create_rejection_from_consumed_response(
            api_error,
            payload=payload,
            authority=authority,
            consumed_response=fabricated_response,
            actual_response=actual_response,
        )
        is None
    )
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_preconsumed_authority_cannot_reenter_request_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    _source, authority = _armed_x_create_transaction(payload)
    bot.consume_transport_authority(
        Path(authority.journal_path),
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
        expected_receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
    )
    request_calls: list[str] = []
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: request_calls.append("request"),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert request_calls == []
    assert error_semantics._issued_rejection_proofs == {}
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_configured_request_coordinator_rejects_loopback_and_custom_auth_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    source, authority = _armed_x_create_transaction(payload)
    server = FakeApiServer(
        {
            "tweet_post_responses": [
                {"status": 403, "body": PRODUCTION_DELETED_REPLY_ERROR}
            ]
        }
    ).start()

    class ResponseReplacingAuth(bot.requests.auth.AuthBase):
        calls = 0

        def __call__(self, request: object) -> object:
            self.calls += 1
            request.register_hook(
                "response",
                lambda *_args, **_kwargs: _x_response(
                    403,
                    PRODUCTION_DELETED_REPLY_ERROR,
                ),
            )
            return request

    custom_auth = ResponseReplacingAuth()
    configured_auth = bot.AUTH
    try:
        with pytest.raises(TypeError):
            journal_module._bind_transport_authority_to_configured_x_request(
                authority,
                payload=payload,
                url=f"{server.url}/2/tweets",
                auth=custom_auth,
            )
        with pytest.raises(
            bot.TransportJournalError,
            match="already installed",
        ):
            journal_module._install_configured_x_request_provider(
                lambda: (f"{server.url}/2/tweets", custom_auth, 30)
            )

        forged = journal_module._BoundXRequestAuthority(
            authority_identity=id(authority),
            source_identity=id(source),
            url=f"{server.url}/2/tweets",
            auth_identity=id(custom_auth),
            timeout_identity=id(30),
            payload_sha256=journal_module.payload_sha256(payload),
        )
        with pytest.raises(
            bot.TransportJournalError,
            match="exact configured authority",
        ):
            journal_module.perform_consumed_x_request(
                Path(authority.journal_path),
                authority,
                request_authority=forged,
                payload=payload,
                expected_receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
                request_kwargs={"json": payload, "allow_redirects": False},
            )

        observed_auth: list[object] = []

        def generic_forbidden(
            *_args: object,
            **kwargs: object,
        ) -> bot.requests.Response:
            observed_auth.append(kwargs.get("auth"))
            return _x_response(403, {"title": "Forbidden"})

        monkeypatch.setattr(bot, "AUTH", custom_auth)
        monkeypatch.setattr(bot.requests, "request", generic_forbidden)
        with pytest.raises(bot.AmbiguousRemotePostOutcome):
            bot.x_request(
                "POST",
                "/2/tweets",
                json=payload,
                ambiguous_write=True,
                _remote_write_authorization=authority,
            )
    finally:
        server.stop()

    assert server.requests == []
    assert observed_auth == [configured_auth]
    assert observed_auth[0] is not custom_auth
    assert custom_auth.calls == 0
    assert error_semantics._issued_rejection_proofs == {}
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_post_install_config_mutation_cannot_redirect_proof_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    provider = journal_module._configured_x_request_provider_identity()
    assert provider is not None
    configured_url, configured_auth, configured_timeout = provider()
    server = FakeApiServer(
        {
            "tweet_post_responses": [
                {"status": 403, "body": PRODUCTION_DELETED_REPLY_ERROR}
            ]
        }
    ).start()
    attacker_url = f"{server.url}/2/tweets"

    class ResponseReplacingAuth(bot.requests.auth.AuthBase):
        calls = 0

        def __call__(self, request: object) -> object:
            self.calls += 1
            request.register_hook(
                "response",
                lambda *_args, **_kwargs: _x_response(
                    403,
                    PRODUCTION_DELETED_REPLY_ERROR,
                ),
            )
            return request

    attacker_auth = ResponseReplacingAuth()
    attacker_timeout = object()
    real_request = bot.requests.request
    observed: list[tuple[str, object, object]] = []

    # The compatibility callable is deliberately non-authoritative.  Even
    # direct mutation of its defaults cannot replace the strong tuple which
    # the journal evaluated and retained at installation.
    monkeypatch.setattr(
        provider,
        "__defaults__",
        (attacker_url, attacker_auth, attacker_timeout),
    )
    assert provider() == (attacker_url, attacker_auth, attacker_timeout)

    def dispatch(
        method: str,
        url: str,
        **kwargs: object,
    ) -> bot.requests.Response:
        observed.append((url, kwargs.get("auth"), kwargs.get("timeout")))
        if url == attacker_url:
            return real_request(method, url, **kwargs)
        raise bot.requests.ConnectionError("sealed test endpoint is unavailable")

    monkeypatch.setattr(bot, "X_BASE", server.url)
    monkeypatch.setattr(bot, "AUTH", attacker_auth)
    monkeypatch.setattr(bot, "request_timeout", lambda: attacker_timeout)
    monkeypatch.setattr(bot.requests, "request", dispatch)
    try:
        with pytest.raises(bot.AmbiguousRemotePostOutcome):
            bot.x_request(
                "POST",
                "/2/tweets",
                json=payload,
                ambiguous_write=True,
                _remote_write_authorization=authority,
            )
    finally:
        server.stop()

    assert observed == [(configured_url, configured_auth, configured_timeout)]
    assert configured_url != attacker_url
    assert attacker_auth.calls == 0
    assert server.requests == []
    assert error_semantics._issued_rejection_proofs == {}
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_test_only_request_provider_reset_refuses_active_transaction() -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    provider = journal_module._configured_x_request_provider_identity()
    assert provider is not None
    configured_url, configured_auth, configured_timeout = provider()

    with pytest.raises(
        bot.TransportJournalError,
        match="active transaction state",
    ):
        journal_module._reset_configured_x_request_provider_for_tests(
            create_url=configured_url,
            auth=configured_auth,
            timeout=configured_timeout,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_test_only_request_provider_reset_is_unavailable_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = journal_module._configured_x_request_provider_identity()
    assert provider is not None
    configured_url, configured_auth, configured_timeout = provider()
    monkeypatch.setenv("MRS_TEST_MODE", "0")

    with pytest.raises(
        bot.TransportJournalError,
        match="outside test mode",
    ):
        journal_module._reset_configured_x_request_provider_for_tests(
            create_url=configured_url,
            auth=configured_auth,
            timeout=configured_timeout,
        )


@pytest.mark.parametrize(
    "create_url",
    (
        "https://api.x.com/2/tweets",
        "http://127.0.0.1%40.attacker.invalid/2/tweets",
        "http://127.0.0.1%00.attacker.invalid/2/tweets",
    ),
    ids=("live-x", "encoded-at-suffix", "encoded-null-suffix"),
)
def test_test_only_request_provider_reset_rejects_non_loopback_without_mutation(
    create_url: str,
) -> None:
    provider = journal_module._configured_x_request_provider_identity()
    assert provider is not None
    configuration = provider()
    _configured_url, configured_auth, configured_timeout = configuration

    with pytest.raises(
        bot.TransportJournalError,
        match="loopback endpoint",
    ):
        journal_module._reset_configured_x_request_provider_for_tests(
            create_url=create_url,
            auth=configured_auth,
            timeout=configured_timeout,
        )

    assert journal_module._configured_x_request_provider_identity() is provider
    assert provider() == configuration
    assert journal_module._bound_x_requests == {}
    assert journal_module._bound_x_request_transactions == {}
    assert journal_module._consumed_x_responses == {}


@pytest.mark.parametrize(
    "case",
    (
        "untrusted-response",
        "wrong-response-url",
        "wrong-request-method",
        "wrong-request-url",
        "wrong-request-body",
    ),
)
def test_response_provenance_mismatch_cannot_prove_reply_rejection(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    configured_url = f"{bot.x_request_base_url('POST', '/2/tweets')}/2/tweets"
    response: object
    if case == "untrusted-response":
        response = SimpleNamespace(
            status_code=403,
            content=json.dumps(PRODUCTION_DELETED_REPLY_ERROR).encode("utf-8"),
            history=[],
            headers={},
            text=json.dumps(PRODUCTION_DELETED_REPLY_ERROR),
            url=configured_url,
        )
    else:
        response = _x_response(403, PRODUCTION_DELETED_REPLY_ERROR)
        response.url = (
            "https://attacker.invalid/2/tweets"
            if case == "wrong-response-url"
            else configured_url
        )
        prepared_payload = (
            {**payload, "text": "different reply"}
            if case == "wrong-request-body"
            else payload
        )
        response.request = bot.requests.Request(
            method="GET" if case == "wrong-request-method" else "POST",
            url=(
                "https://attacker.invalid/2/tweets"
                if case == "wrong-request-url"
                else configured_url
            ),
            json=prepared_payload,
        ).prepare()

    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: response,
    )
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert error_semantics._issued_rejection_proofs == {}
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


@pytest.mark.parametrize(
    "case",
    (
        "redirect-enabled",
        "extra-request-option",
        "error-response-history",
        "success-response-history",
    ),
)
def test_request_coordinator_rejects_redirect_uncertainty(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    _source, authority = _armed_x_create_transaction(payload)
    request_calls: list[str] = []
    request_authority = (
        journal_module._bind_transport_authority_to_configured_x_request(
            authority,
            payload=payload,
        )
    )

    if not case.endswith("response-history"):
        monkeypatch.setattr(
            bot.requests,
            "request",
            lambda *_args, **_kwargs: request_calls.append("request"),
        )
        with pytest.raises(
            bot.TransportJournalError,
            match="exact create route",
        ):
            journal_module.perform_consumed_x_request(
                Path(authority.journal_path),
                authority,
                request_authority=request_authority,
                payload=payload,
                expected_receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
                request_kwargs={
                    "json": payload,
                    "allow_redirects": case != "extra-request-option",
                    **(
                        {"params": {"unexpected": "channel"}}
                        if case == "extra-request-option"
                        else {}
                    ),
                },
            )
        assert request_calls == []
    else:
        response = _x_response(
            201 if case == "success-response-history" else 403,
            (
                {"data": {"id": "999"}}
                if case == "success-response-history"
                else PRODUCTION_DELETED_REPLY_ERROR
            ),
        )
        response.history = [_x_response(307, {"location": "/2/tweets"})]

        def redirected(*_args: object, **_kwargs: object) -> object:
            request_calls.append("request")
            return response

        monkeypatch.setattr(bot.requests, "request", redirected)
        with pytest.raises(
            bot.TransportJournalError,
            match="redirect-free hop",
        ):
            journal_module.perform_consumed_x_request(
                Path(authority.journal_path),
                authority,
                request_authority=request_authority,
                payload=payload,
                expected_receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
                request_kwargs={"json": payload, "allow_redirects": False},
            )
        assert request_calls == ["request"]

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_target_specific_body_on_non_reply_create_remains_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"text": "ordinary non-reply post"}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


@pytest.mark.parametrize(
    "body",
    [
        "not JSON: You attempted to reply to a Tweet that is deleted or not visible to you.",
        (
            '{"detail":"You attempted to reply to a Tweet that is deleted or not '
            'visible to you.","detail":"You attempted to reply to a Tweet that is '
            'deleted or not visible to you."}'
        ),
        '["You attempted to reply to a Tweet that is deleted or not visible to you."]',
        '{"detail":["You attempted to reply to a Tweet that is deleted or not visible to you."]}',
        '{"status":404,"detail":"You attempted to reply to a Tweet that is deleted or not visible to you."}',
        (
            b'{"detail":"You attempted to reply to a Tweet that is deleted or not '
            b'visible to you.","status":403,"title":"Forbidden","type":"about:blank",'
            b'"invalid_utf8":"\xff"}'
        ),
    ],
    ids=(
        "non-json",
        "duplicate-key",
        "non-object",
        "invalid-message-schema",
        "mismatched-body-status",
        "invalid-utf8",
    ),
)
def test_malformed_target_specific_write_response_remains_ambiguous(
    body: str | bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _raw_x_response(403, body),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


@pytest.mark.parametrize(
    "body",
    [
        {**PRODUCTION_DELETED_REPLY_ERROR, "data": {"id": "999"}},
        {**PRODUCTION_DELETED_REPLY_ERROR, "accepted": True},
        {
            **PRODUCTION_DELETED_REPLY_ERROR,
            "type": "urn:x:reply-created-successfully",
        },
        {
            **PRODUCTION_DELETED_REPLY_ERROR,
            "detail": (
                f"{PRODUCTION_DELETED_REPLY_ERROR['detail']} "
                "The reply was created successfully."
            ),
        },
        {
            **PRODUCTION_DELETED_REPLY_ERROR,
            "message": "reply was created successfully",
        },
        {
            "errors": [
                {"detail": PRODUCTION_DELETED_REPLY_ERROR["detail"]},
                {"message": "reply was accepted successfully"},
            ],
            "status": 403,
            "title": "Forbidden",
            "type": "about:blank",
        },
    ],
    ids=(
        "conflicting-data-id",
        "unknown-success-field",
        "success-problem-type",
        "success-in-single-detail",
        "extra-success-message",
        "second-success-error",
    ),
)
def test_conflicting_or_extended_target_error_envelope_remains_ambiguous(
    body: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(403, body),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_target_specific_body_on_historical_context_reply_remains_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "unit_test": True,
    }
    payload = {
        "text": "historical context",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    bot.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        receipt,
        durable=True,
    )
    source = bot.bind_transport_source(
        receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        expected_receipt=receipt,
        lane="historical_context_reply",
        payload=payload,
        validator_id="unit-test-context-source-binding-v2",
        validator=lambda lane, observed, body: bool(
            lane == "historical_context_reply"
            and observed == receipt
            and body == payload
        ),
    )
    prepared = bot.begin_transport_transaction(
        receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_binding=source,
    )
    authority = bot.arm_transport_transaction(
        Path(prepared.journal_path),
        prepared,
        mutation_authority=bot.transaction_mutation_authority(
            "focused historical reply arming"
        ),
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()


@pytest.mark.parametrize(
    "transport_error",
    [
        pytest.param(bot.requests.Timeout("response timeout"), id="timeout"),
        pytest.param(
            bot.requests.ConnectionError("connection lost"),
            id="connection-loss",
        ),
    ],
)
def test_reply_create_transport_failure_remains_ambiguous(
    transport_error: bot.requests.RequestException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(transport_error),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_unexpected_response_handling_failure_invalidates_consumed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    consume_calls = 0
    real_consume = journal_module.consume_transport_authority

    def capture_consumption(*args: object, **kwargs: object) -> None:
        nonlocal consume_calls
        real_consume(*args, **kwargs)
        consume_calls += 1

    class ExplodingResponse:
        @property
        def status_code(self) -> int:
            raise RuntimeError("injected status handling failure")

    monkeypatch.setattr(
        journal_module,
        "consume_transport_authority",
        capture_consumption,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: ExplodingResponse(),
    )

    with pytest.raises(RuntimeError, match="status handling failure"):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert consume_calls == 1
    assert journal_module._consumed_x_responses == {}
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_post_bind_response_handling_failure_discards_actual_response_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    captured_proofs: list[object] = []
    real_perform = bot.perform_consumed_x_request

    def capture_proof(*args: object, **kwargs: object) -> object:
        result = real_perform(*args, **kwargs)
        captured_proofs.append(result[2])
        return result

    real_debug = bot.log.debug

    def fail_after_bind(message: str, *args: object, **kwargs: object) -> None:
        if message == "X response status: %s":
            raise RuntimeError("injected post-bind logging failure")
        real_debug(message, *args, **kwargs)

    monkeypatch.setattr(
        bot,
        "perform_consumed_x_request",
        capture_proof,
    )
    monkeypatch.setattr(bot.log, "debug", fail_after_bind)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )

    with pytest.raises(RuntimeError, match="post-bind logging failure"):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert len(captured_proofs) == 1
    assert journal_module._consumed_x_responses == {}
    assert id(captured_proofs[0]) not in error_semantics._issued_rejection_proofs
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_actual_response_capability_publication_interruption_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)

    class PublishThenRaise(dict[int, tuple[object, ...]]):
        def __setitem__(self, key: int, value: tuple[object, ...]) -> None:
            super().__setitem__(key, value)
            raise RuntimeError("injected response capability interruption")

    interrupted_registry = PublishThenRaise()
    monkeypatch.setattr(
        journal_module,
        "_consumed_x_responses",
        interrupted_registry,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )

    with pytest.raises(RuntimeError, match="response capability interruption"):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert interrupted_registry == {}
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_proved_exception_construction_failure_invalidates_issued_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)
    captured_proofs: list[object] = []
    real_exception = bot.ProvedRemotePostNonSuccess

    class ExplodingProvedRemotePostNonSuccess(real_exception):
        def __init__(
            self,
            message: str,
            *,
            remote_non_success_proof: object,
            **kwargs: object,
        ) -> None:
            captured_proofs.append(remote_non_success_proof)
            raise RuntimeError("injected proved-exception construction failure")

    monkeypatch.setattr(
        bot,
        "ProvedRemotePostNonSuccess",
        ExplodingProvedRemotePostNonSuccess,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )

    with pytest.raises(RuntimeError, match="proved-exception construction"):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert len(captured_proofs) == 1
    assert error_semantics.reply_create_rejection_payload(
        captured_proofs[0]
    ) is None
    assert id(captured_proofs[0]) not in error_semantics._issued_rejection_proofs
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_proof_publication_interruption_rolls_back_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "text": "unit reply",
        "reply": {"in_reply_to_tweet_id": "100"},
    }
    authority = _armed_x_create_authority(payload)

    class PublishThenRaise(dict[int, tuple[object, ...]]):
        def __setitem__(self, key: int, value: tuple[object, ...]) -> None:
            super().__setitem__(key, value)
            raise RuntimeError("injected proof publication interruption")

    interrupted_registry = PublishThenRaise()
    monkeypatch.setattr(
        error_semantics,
        "_issued_rejection_proofs",
        interrupted_registry,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )

    with pytest.raises(RuntimeError, match="proof publication interruption"):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=authority,
        )

    assert interrupted_registry == {}
    assert Path(authority.journal_path).exists()
    assert Path(authority.fence_path).exists()


def test_deleted_mention_reply_is_terminal_without_transport_barriers_or_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    provider_calls: list[str] = []
    remote_calls: list[str] = []
    _configure_approved_mention_candidate(
        state,
        monkeypatch,
        provider_calls=provider_calls,
    )

    monkeypatch.setattr(
        bot.requests,
        "request",
        _existing_reply_target_then_deleted_create(
            "100",
            remote_calls=remote_calls,
        ),
    )

    assert (
        bot.maybe_reply_to_mentions(state)
        == bot.NORMAL_CHECK_STATUS_CHECKED
    )

    terminal = bot.terminal_reply_evaluation(state, "100")
    assert terminal is not None
    assert terminal["outcome"] == "reply_not_permitted"
    assert terminal["reason"] == "x_reply_not_permitted"
    assert state["daily_reply_count"] == 0
    assert state["last_reply_epoch"] == 0
    assert state["daily_replied_author_ids"] == []
    assert not state.get("pending_ai_reply_drafts")
    assert provider_calls == ["called"]
    assert remote_calls == ["GET /2/tweets/100", "POST /2/tweets"]
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    assert not journal_path.exists()
    assert not bot.fence_path_for_journal(journal_path).exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is False

    restarted = bot.load_state()
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda *_args: pytest.fail(
            "terminal target must not rebuild context after restart"
        ),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(lambda *_args, **_kwargs: pytest.fail(
            "terminal target must not call a provider after restart"
        )),
    )
    assert (
        bot.maybe_reply_to_mentions(restarted)
        == bot.NORMAL_CHECK_STATUS_CHECKED
    )
    assert provider_calls == ["called"]
    assert remote_calls == ["GET /2/tweets/100", "POST /2/tweets"]
    assert bot.terminal_reply_evaluation(restarted, "100") is not None

    # The proved rejection released the global transaction barrier: a later,
    # unrelated authorised reply can cross transport and confirm normally.
    later_sending = unit_sending_v4_reply_receipt(
        target_id="101",
        author_id="201",
        text="Thank you for sharing.",
        epoch=2_000_000_001,
        attempt_epoch=2_000_000_001,
    )

    def confirmed_later(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        remote_calls.append("later")
        return _x_response(201, {"data": {"id": "900001"}})

    monkeypatch.setattr(bot.requests, "request", confirmed_later)
    response, confirmed = bot.post_conversational_reply_with_durable_identity(
        state=restarted,
        receipt_template=later_sending,
        reply_text=str(later_sending["reply_text"]),
        reply_to_id=str(later_sending["target_id"]),
        made_with_ai=False,
        lane="mention",
    )
    assert response == {"data": {"id": "900001"}}
    bot.apply_confirmed_reply_receipt(restarted, confirmed)
    from mrs_bot_state_generation import record_receipt_commit

    record_receipt_commit(restarted, confirmed)
    proof = bot.save_state(restarted, durable=True)
    bot.retire_lane_transport_journal_if_present(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        receipt=confirmed,
        lane="conversational_reply",
        post_id="900001",
        commit_proof=proof,
    )
    bot.remove_confirmed_reply_receipt(confirmed, commit_proof=proof)
    assert remote_calls == ["GET /2/tweets/100", "POST /2/tweets", "later"]
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_deleted_quote_tweet_reply_is_terminal_without_barriers_or_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    provider_calls: list[str] = []
    remote_calls: list[str] = []
    _configure_approved_quote_candidate(
        state,
        monkeypatch,
        provider_calls=provider_calls,
    )

    monkeypatch.setattr(
        bot.requests,
        "request",
        _existing_reply_target_then_deleted_create(
            "910",
            remote_calls=remote_calls,
        ),
    )

    assert (
        bot.maybe_reply_to_quote_tweets(state)
        == bot.QUOTE_CHECK_STATUS_CHECKED
    )

    terminal = bot.terminal_reply_evaluation(state, "910")
    assert terminal is not None
    assert terminal["outcome"] == "reply_not_permitted"
    assert terminal["reason"] == "x_reply_not_permitted"
    assert state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 0
    assert state["last_reply_epoch"] == 0
    assert state["daily_replied_author_ids"] == []
    assert "910" in state["skipped_quote_post_ids"]
    assert not state.get("pending_ai_reply_drafts")
    assert provider_calls == ["called"]
    assert remote_calls == ["GET /2/tweets/910", "POST /2/tweets"]
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    assert not journal_path.exists()
    assert not bot.fence_path_for_journal(journal_path).exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_proved_rejection_journal_retirement_failure_preserves_barriers_and_is_not_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    provider_calls: list[str] = []
    _configure_approved_mention_candidate(
        state,
        monkeypatch,
        provider_calls=provider_calls,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        _existing_reply_target_then_deleted_create("100"),
    )
    real_unlink = journal_module._unlink_exact_stable_file

    def fail_proved_journal_retirement(*args: object, **kwargs: object) -> None:
        if kwargs.get("label") == "proved-non-success transport journal":
            raise bot.TransportJournalError(
                "injected proved rejection journal retirement failure"
            )
        real_unlink(*args, **kwargs)

    monkeypatch.setattr(
        journal_module,
        "_unlink_exact_stable_file",
        fail_proved_journal_retirement,
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_mentions(state)

    assert bot.terminal_reply_evaluation(state, "100") is None
    assert state["daily_reply_count"] == 0
    assert state.get("pending_ai_reply_drafts")
    assert bot.load_confirmed_reply_receipt()[0] == "sending"
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    assert journal_path.exists()
    assert bot.fence_path_for_journal(journal_path).exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_proved_rejection_journal_close_failure_invalidates_proof_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    provider_calls: list[str] = []
    _configure_approved_mention_candidate(
        state,
        monkeypatch,
        provider_calls=provider_calls,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        _existing_reply_target_then_deleted_create("100"),
    )
    captured_proofs: list[object] = []
    real_retire = (
        bot.retire_consumed_transport_transaction_after_proved_remote_non_success
    )

    def capture_proof(*args: object, **kwargs: object) -> None:
        captured_proofs.append(kwargs["remote_non_success_proof"])
        real_retire(*args, **kwargs)

    monkeypatch.setattr(
        bot,
        "retire_consumed_transport_transaction_after_proved_remote_non_success",
        capture_proof,
    )
    real_unlink = journal_module._unlink_exact_stable_file
    close_target: int | None = None

    def track_final_unlink(
        directory_fd: int,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal close_target
        real_unlink(directory_fd, *args, **kwargs)
        if kwargs.get("label") == "proved-non-success transport fence":
            close_target = directory_fd

    real_close = journal_module.os.close

    def fail_final_directory_close(descriptor: int) -> None:
        nonlocal close_target
        if descriptor == close_target:
            close_target = None
            real_close(descriptor)
            raise OSError("injected proved rejection directory close failure")
        real_close(descriptor)

    monkeypatch.setattr(
        journal_module,
        "_unlink_exact_stable_file",
        track_final_unlink,
    )
    monkeypatch.setattr(journal_module.os, "close", fail_final_directory_close)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_mentions(state)

    assert captured_proofs
    assert error_semantics.reply_create_rejection_payload(
        captured_proofs[0]
    ) is None
    assert bot.terminal_reply_evaluation(state, "100") is None
    assert state["daily_reply_count"] == 0
    assert bot.load_confirmed_reply_receipt()[0] == "sending"
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    assert not journal_path.exists()
    assert not bot.fence_path_for_journal(journal_path).exists()
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_proved_rejection_receipt_retirement_failure_is_terminal_but_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    provider_calls: list[str] = []
    _configure_approved_mention_candidate(
        state,
        monkeypatch,
        provider_calls=provider_calls,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        _existing_reply_target_then_deleted_create("100"),
    )
    claimed_proofs: list[object] = []
    real_claim = bot.claim_reply_create_rejection_for_receipt_retirement

    def capture_claim(proof: object, **kwargs: object) -> bool:
        claimed_proofs.append(proof)
        return real_claim(proof, **kwargs)

    monkeypatch.setattr(
        bot,
        "claim_reply_create_rejection_for_receipt_retirement",
        capture_claim,
    )
    monkeypatch.setattr(
        bot,
        "remove_confirmed_reply_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected sending receipt retirement failure")
        ),
    )

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot.maybe_reply_to_mentions(state)

    terminal = bot.terminal_reply_evaluation(state, "100")
    assert terminal is not None
    assert terminal["outcome"] == "reply_not_permitted"
    assert claimed_proofs
    assert error_semantics.reply_create_rejection_payload(
        claimed_proofs[0]
    ) is None
    assert state["daily_reply_count"] == 0
    assert not state.get("pending_ai_reply_drafts")
    assert bot.load_confirmed_reply_receipt()[0] == "sending"
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    assert not journal_path.exists()
    assert not bot.fence_path_for_journal(journal_path).exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_proved_rejection_missing_receipt_at_claim_latches_terminal_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    provider_calls: list[str] = []
    _configure_approved_mention_candidate(
        state,
        monkeypatch,
        provider_calls=provider_calls,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        _existing_reply_target_then_deleted_create("100"),
    )
    captured_proofs: list[object] = []
    real_claim = bot.claim_reply_create_rejection_for_receipt_retirement

    def remove_before_claim(proof: object, **kwargs: object) -> bool:
        captured_proofs.append(proof)
        bot.CONFIRMED_REPLY_RECEIPT_FILE.unlink()
        return real_claim(proof, **kwargs)

    monkeypatch.setattr(
        bot,
        "claim_reply_create_rejection_for_receipt_retirement",
        remove_before_claim,
    )

    with pytest.raises(
        bot.ConfirmedReplyLocalPersistenceError,
        match="retirement unresolved",
    ):
        bot.maybe_reply_to_mentions(state)

    terminal = bot.terminal_reply_evaluation(state, "100")
    assert terminal is not None
    assert terminal["outcome"] == "reply_not_permitted"
    assert bot.json_file_matches(bot.STATE_FILE, state)
    assert state["daily_reply_count"] == 0
    assert not state.get("pending_ai_reply_drafts")
    assert captured_proofs
    assert error_semantics.reply_create_rejection_payload(
        captured_proofs[0]
    ) is not None
    assert bot.load_confirmed_reply_receipt()[0] == "absent"
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    assert not journal_path.exists()
    assert not bot.fence_path_for_journal(journal_path).exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.block_if_ambiguous_remote_post()


def test_proved_rejection_cannot_retire_replaced_same_target_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    receipt = unit_sending_v4_reply_receipt(
        target_id="100",
        author_id="200",
        text="unit reply",
        epoch=2_000_000_000,
        attempt_epoch=2_000_000_000,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: _x_response(
            403,
            PRODUCTION_DELETED_REPLY_ERROR,
        ),
    )
    health_updates: list[str] = []
    monkeypatch.setattr(
        bot,
        "report_bot_health_progress",
        lambda phase, **_kwargs: health_updates.append(phase),
    )

    with pytest.raises(bot.ProvedRemotePostNonSuccess) as caught:
        bot.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=receipt,
            reply_text=str(receipt["reply_text"]),
            reply_to_id=str(receipt["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    replacement = unit_sending_v4_reply_receipt(
        target_id="100",
        author_id="999",
        text="unit reply",
        epoch=2_000_000_001,
        attempt_epoch=2_000_000_001,
    )
    assert bot.sending_reply_receipt_is_semantically_valid(replacement)
    bot.atomic_write_json(
        bot.CONFIRMED_REPLY_RECEIPT_FILE,
        replacement,
        durable=True,
    )

    with pytest.raises(
        bot.ConfirmedReplyLocalPersistenceError,
        match="retirement unresolved",
    ):
        bot.retire_proved_rejected_conversational_reply_receipt(
            replacement,
            caught.value,
        )

    assert error_semantics.reply_create_rejection_payload(
        caught.value.remote_non_success_proof
    ) is not None
    assert bot.load_confirmed_reply_receipt() == ("sending", replacement)
    journal_path = bot.journal_path_for_receipt(
        bot.CONFIRMED_REPLY_RECEIPT_FILE
    )
    assert not journal_path.exists()
    assert not bot.fence_path_for_journal(journal_path).exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert health_updates == []


def test_x_create_redirect_is_not_followed_and_is_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def redirected(
        _method: str,
        _url: str,
        **kwargs: object,
    ) -> bot.requests.Response:
        calls.append(kwargs)
        response = _x_response(307, {"detail": "redirect"})
        response.headers["Location"] = "https://example.invalid/other-create"
        return response

    monkeypatch.setattr(bot.requests, "request", redirected)

    payload = {"text": "unit"}
    authority = _armed_x_create_authority(payload)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            allow_redirects=True,
            _remote_write_authorization=authority,
        )

    assert calls == []


def test_regular_generic_4xx_retains_attempt_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    quote_hash = bot.quote_text_hash("Good quote.")
    patch_completed_research_quotes(monkeypatch, bot, lambda: {quote_hash})
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    remote_calls = 0

    def generic_400(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(400, {"errors": [{"detail": "generic rejection"}]})

    monkeypatch.setattr(bot.requests, "request", generic_400)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    receipt_bytes = bot.REGULAR_POST_RECEIPT_FILE.read_bytes()
    assert remote_calls == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(lines_used, images_used, state)

    assert remote_calls == 1
    assert bot.REGULAR_POST_RECEIPT_FILE.read_bytes() == receipt_bytes


def test_meme_generic_4xx_retains_attempt_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, _receipt_path = configure_simple_meme_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    remote_calls = 0

    def generic_403(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(403, {"errors": [{"detail": "generic forbidden"}]})

    monkeypatch.setattr(bot.requests, "request", generic_403)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    receipt_bytes = bot.MEME_POST_RECEIPT_FILE.read_bytes()
    assert remote_calls == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)

    assert remote_calls == 1
    assert bot.MEME_POST_RECEIPT_FILE.read_bytes() == receipt_bytes


def test_regular_handler_retires_transaction_when_pause_follows_media_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production quote handler owns definite pretransport cleanup."""

    actual_create_post = bot.create_post
    actual_upload_media = bot.upload_media
    actual_handoff = bot.handoff_confirmed_media_upload_to_main_attempt
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    quote_hash = bot.quote_text_hash("Good quote.")
    patch_completed_research_quotes(monkeypatch, bot, lambda: {quote_hash})
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    monkeypatch.setattr(bot, "upload_media", actual_upload_media)
    paused = False
    handoffs = 0
    transport_calls: list[str] = []

    def handoff_then_pause(
        attempt: dict,
        authority: bot.TransportAuthority,
    ) -> None:
        nonlocal handoffs, paused
        actual_handoff(attempt, authority)
        handoffs += 1
        paused = True

    def local_media_transport(
        method: str,
        url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        transport_calls.append(f"{method.upper()} {url}")
        assert method.upper() == "POST"
        assert url.endswith("/2/media/upload")
        return _x_response(201, {"data": {"id": "780001"}})

    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        handoff_then_pause,
    )
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: paused)
    monkeypatch.setattr(bot.requests, "request", local_media_transport)

    with pytest.raises(bot.RemoteOperationsPaused):
        bot.post_random_quote(lines_used, images_used, state)

    assert handoffs == 1
    assert len(transport_calls) == 1
    assert transport_calls[0].endswith("/2/media/upload")
    assert not bot.MEDIA_UPLOAD_RECEIPT_FILE.exists()
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.journal_path_for_receipt(bot.REGULAR_POST_RECEIPT_FILE).exists()
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_meme_handler_retires_transaction_when_pause_follows_media_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production meme handler owns definite pretransport cleanup."""

    actual_create_post = bot.create_post
    actual_upload_media = bot.upload_media
    actual_handoff = bot.handoff_confirmed_media_upload_to_main_attempt
    state, _receipt_path = configure_simple_meme_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    monkeypatch.setattr(bot, "upload_media", actual_upload_media)
    paused = False
    handoffs = 0
    transport_calls: list[str] = []

    def handoff_then_pause(
        attempt: dict,
        authority: bot.TransportAuthority,
    ) -> None:
        nonlocal handoffs, paused
        actual_handoff(attempt, authority)
        handoffs += 1
        paused = True

    def local_media_transport(
        method: str,
        url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        transport_calls.append(f"{method.upper()} {url}")
        assert method.upper() == "POST"
        assert url.endswith("/2/media/upload")
        return _x_response(201, {"data": {"id": "780001"}})

    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        handoff_then_pause,
    )
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: paused)
    monkeypatch.setattr(bot.requests, "request", local_media_transport)

    with pytest.raises(bot.RemoteOperationsPaused):
        bot.post_next_meme(state)

    assert handoffs == 1
    assert len(transport_calls) == 1
    assert transport_calls[0].endswith("/2/media/upload")
    assert not bot.MEDIA_UPLOAD_RECEIPT_FILE.exists()
    assert not bot.MEME_POST_RECEIPT_FILE.exists()
    assert not bot.journal_path_for_receipt(bot.MEME_POST_RECEIPT_FILE).exists()
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_conversational_generic_4xx_retains_sending_receipt_and_blocks_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    remote_calls = 0

    def generic_404(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(404, {"errors": [{"detail": "generic not found"}]})

    monkeypatch.setattr(bot.requests, "request", generic_404)
    health_updates: list[str] = []
    monkeypatch.setattr(
        bot,
        "report_bot_health_progress",
        lambda phase, **_kwargs: health_updates.append(phase),
    )

    def invoke() -> tuple[dict, dict]:
        return bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        invoke()

    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    receipt_bytes = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    assert remote_calls == 1

    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="unresolved"):
        invoke()

    assert remote_calls == 1
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes
    assert health_updates == []


def test_historical_context_generic_4xx_retains_sending_receipt_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HistoricalContextReplyStore(
        tmp_path / "context-history.json",
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    remote_calls = 0

    def generic_409(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(409, {"errors": [{"detail": "generic conflict"}]})

    monkeypatch.setattr(bot.requests, "request", generic_409)
    _outbox, callbacks = _claimed_context_outbox_callbacks(
        parent_post_id="111",
        quote_id="a" * 64,
        quote_text="A reviewed historical-context quotation.",
        started_epoch=1_800_000_000,
    )

    def create_context_post(**kwargs: object) -> dict:
        return bot.create_post(**kwargs)

    with pytest.raises(AmbiguousContextReplyOutcome):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=create_context_post,
            now_epoch=lambda: 1_800_000_000,
            **callbacks,
        )

    sending = json.loads(store.receipt_path.read_text(encoding="utf-8"))
    assert sending["lifecycle_state"] == "sending"
    receipt_bytes = store.receipt_path.read_bytes()
    assert remote_calls == 1

    with pytest.raises(AmbiguousContextReplyOutcome):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **_kwargs: pytest.fail(
                "restart must not repeat an ambiguous context create"
            ),
            now_epoch=lambda: 1_800_000_001,
        )

    assert remote_calls == 1
    assert store.receipt_path.read_bytes() == receipt_bytes


def test_historical_context_target_error_stays_ambiguous_in_real_store_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HistoricalContextReplyStore(
        tmp_path / "context-history.json",
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    remote_calls: list[str] = []

    def target_error(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        remote_calls.append("request")
        return _x_response(403, PRODUCTION_DELETED_REPLY_ERROR)

    monkeypatch.setattr(bot.requests, "request", target_error)
    _outbox, callbacks = _claimed_context_outbox_callbacks(
        parent_post_id="111",
        quote_id="b" * 64,
        quote_text="A reviewed historical-context quotation.",
        started_epoch=1_800_000_000,
    )

    with pytest.raises(AmbiguousContextReplyOutcome):
        store.post(
            parent_post_id="111",
            quote_id="b" * 64,
            reply_text="Context",
            create_post=bot.create_post,
            now_epoch=lambda: 1_800_000_000,
            **callbacks,
        )

    assert remote_calls == ["request"]
    sending = json.loads(store.receipt_path.read_text(encoding="utf-8"))
    assert sending["lifecycle_state"] == "sending"
    journal_path = bot.journal_path_for_receipt(store.receipt_path)
    assert journal_path.exists()
    assert bot.fence_path_for_journal(journal_path).exists()
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_exact_owning_historical_context_create_is_allowed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transaction which durably wrote the exact receipt may send once."""

    store = HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    remote_calls: list[dict[str, object]] = []

    def accepted(
        _method: str,
        _url: str,
        **kwargs: object,
    ) -> bot.requests.Response:
        remote_calls.append(kwargs)
        return _x_response(201, {"data": {"id": "222"}})

    monkeypatch.setattr(bot.requests, "request", accepted)
    _outbox, callbacks = _claimed_context_outbox_callbacks(
        parent_post_id="111",
        quote_id="a" * 64,
        quote_text="A reviewed historical-context quotation.",
        started_epoch=1_800_000_000,
    )

    result = store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text="Context — exact transaction owner.",
        create_post=bot.create_post,
        now_epoch=lambda: 1_800_000_000,
        **callbacks,
    )

    assert result["status"] == "completed"
    assert result["reply_post_id"] == "222"
    assert len(remote_calls) == 1
    assert not store.receipt_path.exists()
    assert not bot.journal_path_for_receipt(store.receipt_path).exists()
    completed = store.history()["items"]["111"]
    assert completed["status"] == "completed"
    assert completed["reply_post_id"] == "222"


def test_historical_context_remote_phase_callback_runs_after_arm_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    events: list[str] = []
    real_arm = bot.arm_transport_transaction
    outbox, callbacks = _claimed_context_outbox_callbacks(
        parent_post_id="112",
        quote_id="b" * 64,
        quote_text="A reviewed historical-context quotation.",
        started_epoch=1_800_000_000,
    )

    def recording_arm(*args: object, **kwargs: object) -> bot.TransportAuthority:
        authority = real_arm(*args, **kwargs)
        events.append("journal_armed")
        return authority

    def mark_remote_started() -> None:
        assert bot.transport_journal_is_blocking(
            bot.journal_path_for_receipt(store.receipt_path)
        )
        callbacks["on_remote_transaction_started"]()
        events.append("remote_phase_durable")

    def accepted(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        events.append("transport")
        return _x_response(201, {"data": {"id": "223"}})

    monkeypatch.setattr(bot, "arm_transport_transaction", recording_arm)
    monkeypatch.setattr(bot.requests, "request", accepted)

    result = store.post(
        parent_post_id="112",
        quote_id="b" * 64,
        reply_text="Context — durable remote phase ordering.",
        create_post=bot.create_post,
        now_epoch=lambda: 1_800_000_000,
        on_source_receipt_published=callbacks["on_source_receipt_published"],
        on_remote_transaction_started=mark_remote_started,
        on_confirmed_receipt=callbacks["on_confirmed_receipt"],
    )

    assert result["status"] == "completed"
    assert events == ["journal_armed", "remote_phase_durable", "transport"]


def test_historical_context_remote_phase_failure_never_reaches_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "phase-persistence failure must stop before transport"
        ),
    )

    with pytest.raises(
        AmbiguousContextReplyOutcome,
        match="remote context reply outcome is not proved",
    ):
        store.post(
            parent_post_id="113",
            quote_id="c" * 64,
            reply_text="Context — phase persistence failed.",
            create_post=bot.create_post,
            now_epoch=lambda: 1_800_000_000,
            on_remote_transaction_started=lambda: (_ for _ in ()).throw(
                OSError("injected outbox phase write failure")
            ),
        )

    assert bot.transport_journal_is_blocking(
        bot.journal_path_for_receipt(store.receipt_path)
    )
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_regular_success_retires_journal_before_lane_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_ = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    quote_hash = bot.quote_text_hash("Good quote.")
    patch_completed_research_quotes(monkeypatch, bot, lambda: {quote_hash})
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_a, **_k: _x_response(201, {"data": {"id": "950001"}}),
    )

    bot.post_random_quote(lines_used, images_used, state)

    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.journal_path_for_receipt(bot.REGULAR_POST_RECEIPT_FILE).exists()


def test_meme_success_retires_journal_before_lane_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, _ = configure_simple_meme_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_a, **_k: _x_response(201, {"data": {"id": "970001"}}),
    )

    bot.post_next_meme(state)

    assert not bot.MEME_POST_RECEIPT_FILE.exists()
    assert not bot.journal_path_for_receipt(bot.MEME_POST_RECEIPT_FILE).exists()


def test_conversational_success_retires_journal_only_after_state_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = unit_sending_v4_reply_receipt()
    health_updates: list[str] = []
    monkeypatch.setattr(
        bot,
        "report_bot_health_progress",
        lambda phase, **_kwargs: health_updates.append(phase),
    )
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_a, **_k: _x_response(201, {"data": {"id": "980001"}}),
    )
    state = bot.default_state()

    _response, confirmed = bot.post_conversational_reply_with_durable_identity(
        state=state,
        receipt_template=receipt,
        reply_text=str(receipt["reply_text"]),
        reply_to_id=str(receipt["target_id"]),
        made_with_ai=False,
        lane=str(receipt["candidate_source"]),
    )
    journal_path = bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    assert journal_path.exists()
    bot.apply_confirmed_reply_receipt(state, confirmed)
    from mrs_bot_state_generation import record_receipt_commit

    record_receipt_commit(state, confirmed)
    proof = bot.save_state(state, durable=True)
    bot.retire_lane_transport_journal_if_present(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        receipt=confirmed,
        lane="conversational_reply",
        post_id="980001",
        commit_proof=proof,
    )
    bot.remove_confirmed_reply_receipt(confirmed, commit_proof=proof)

    assert not journal_path.exists()
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert health_updates == []


def test_prepared_receipt_authority_requires_the_exact_durable_file() -> None:
    """An in-memory receipt cannot authorise a send after its file vanished."""

    conversational = unit_sending_reply_receipt()
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="exact durable sending receipt",
    ):
        bot.block_if_ambiguous_remote_post(
            prepared_conversational_reply_receipt=conversational,
        )

    main_attempt = bot.build_main_post_attempt(
        lane="daily_meme",
        text="",
        media_ids=["123"],
        made_with_ai=False,
        selected_identity={"meme_basename": "unit.png"},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
            "fallback_hour": int(bot.MEME_FALLBACK_HOUR),
            "fallback_minute": int(bot.MEME_FALLBACK_MINUTE),
            "image_summary": "Unit meme image.",
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=1_800_000_000,
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="exact durable sending receipt",
    ):
        bot.block_if_ambiguous_remote_post(
            prepared_main_post_attempt=main_attempt,
        )

    historical = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "111",
        "quote_id": "a" * 64,
        "reply_text": "Context",
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-07-31T12:00:00Z",
        "attempt_number": 1,
    }
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="not durably present",
    ):
        bot.block_if_ambiguous_remote_post(
            prepared_historical_context_reply_receipt=historical,
        )


def test_made_with_ai_generic_400_never_triggers_second_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_calls = 0

    def generic_400_with_field_name(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(
            400,
            {
                "errors": [
                    {
                        "detail": (
                            "The proxy echoed made_with_ai but supplied no "
                            "structured proof that the create was rejected"
                        )
                    }
                ]
            },
        )

    monkeypatch.setattr(bot.requests, "request", generic_400_with_field_name)
    receipt = unit_sending_reply_receipt(text="unit reply")
    bot.write_sending_reply_receipt(receipt)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            "unit reply",
            reply_to_id=str(receipt["target_id"]),
            made_with_ai=True,
            prepared_conversational_reply_receipt=receipt,
        )

    assert remote_calls == 1
