from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

import mrsMThatcher2 as bot
from historical_context_formatter import (
    AmbiguousContextReplyOutcome,
    HistoricalContextReplyStore,
)
from tests.helpers.protocol_activation import create_test_protocol_activation
from tests.test_unit_helpers import (
    UNIT_REPLY_REPOSITORY,
    configure_simple_meme_post,
    configure_simple_quote_post,
    unit_sending_reply_receipt,
    unit_sending_v4_reply_receipt,
)


def _x_response(status_code: int, body: object) -> bot.requests.Response:
    response = bot.requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def _armed_x_create_authority(payload: dict[str, object]) -> bot.TransportAuthority:
    """Create one exact journal authority for a direct transport unit test."""

    receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "unit_test": True,
    }
    bot.atomic_write_json(bot.CONFIRMED_REPLY_RECEIPT_FILE, receipt, durable=True)
    prepared = bot.begin_transport_transaction(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        expected_receipt=receipt,
        lane="conversational_reply",
        payload=payload,
        source_validator_id="unit-test-source-binding-v2",
        source_validator=lambda lane, observed, body: bool(
            lane == "conversational_reply"
            and observed == receipt
            and body == payload
        ),
    )
    return bot.arm_transport_transaction(
        Path(prepared.journal_path),
        prepared,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport arming"
        ),
    )


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
        lambda *_args, **_kwargs: request_calls.append("request"),
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


@pytest.fixture(autouse=True)
def isolate_remote_write_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every durable ambiguity barrier inside one test directory."""
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(
        bot,
        "REGULAR_POST_RECEIPT_FILE",
        tmp_path / "regular_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        tmp_path / "meme_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "CONFIRMED_REPLY_RECEIPT_FILE",
        tmp_path / "confirmed_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "MEDIA_UPLOAD_RECEIPT_FILE",
        tmp_path / "remote_media_upload_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_FILE",
        tmp_path / "ambiguous_post_outcome.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        tmp_path / "ambiguous_post_outcome.restart_barrier.json",
    )
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        tmp_path / bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
    )
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE",
        tmp_path / "historical_context_reply_history.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        tmp_path / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        tmp_path / "historical_context_reply_outbox.json",
    )
    monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {
            "signature": None,
            "data": {},
            "has_valid": False,
            "failure_signature": None,
        },
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_SEMANTIC_GATE",
        SimpleNamespace(
            available=True,
            ledger_sha256="unit-test-ledger",
            projection_sha256="unit-test-projection",
            disposition=lambda _quote_id: None,
        ),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)


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
    monkeypatch.setattr(bot, "completed_research_quote_hashes", lambda: {quote_hash})
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
    monkeypatch.setattr(bot, "completed_research_quote_hashes", lambda: {quote_hash})
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


def test_historical_context_generic_4xx_retains_sending_receipt_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HistoricalContextReplyStore(
        tmp_path / "context-history.json",
        tmp_path / "context-receipt.json",
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        store.receipt_path,
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

    def create_context_post(**kwargs: object) -> dict:
        return bot.create_post(**kwargs)

    with pytest.raises(AmbiguousContextReplyOutcome):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=create_context_post,
            now_epoch=lambda: 123,
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
            now_epoch=lambda: 124,
        )

    assert remote_calls == 1
    assert store.receipt_path.read_bytes() == receipt_bytes


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

    result = store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text="Context — exact transaction owner.",
        create_post=bot.create_post,
        now_epoch=lambda: 1_800_000_000,
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

    def recording_arm(*args: object, **kwargs: object) -> bot.TransportAuthority:
        authority = real_arm(*args, **kwargs)
        events.append("journal_armed")
        return authority

    def mark_remote_started() -> None:
        assert bot.transport_journal_is_blocking(
            bot.journal_path_for_receipt(store.receipt_path)
        )
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
        on_remote_transaction_started=mark_remote_started,
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
    monkeypatch.setattr(bot, "completed_research_quote_hashes", lambda: {quote_hash})
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
    bot.save_state(state, durable=True)
    bot.retire_lane_transport_journal_if_present(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        receipt=confirmed,
        lane="conversational_reply",
        post_id="980001",
    )
    bot.remove_confirmed_reply_receipt(confirmed)

    assert not journal_path.exists()
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()


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
        recovery_plan={"next_schedule_mode": "fallback"},
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
